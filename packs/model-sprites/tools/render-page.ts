// The browser half of the model sprite renderer. render-frames.ts opens this page in a headless Chrome
// and calls `window.renderSprites(settings)`: the page loads the animated GLB, poses each clip at each frame time,
// renders it from the 2:1 dimetric camera for the 8 view facings, and reduces each render to a palette
// pixel image.

import {
  AnimationMixer,
  type AnimationAction,
  Box3,
  DirectionalLight,
  Group,
  HemisphereLight,
  LoopOnce,
  LoopRepeat,
  Mesh,
  MeshStandardMaterial,
  OrthographicCamera,
  Scene,
  Vector3,
  WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { IMAGE_PIXELS } from './pixels';
import { DecalLayer } from './decals';
import { PartLayer } from './parts';
import { PoseLayer, readTrunk, setTrunk } from './pose';
import { RotorLayer } from './rotors';
import { WeaponLayer } from './weapon';
import { WingLayer } from './wings';
import {
  FACINGS,
  facingYaw,
  frameTimes,
  parsePalette,
  PITCH_DEGREES,
  pixelate,
  type RenderedAnimation,
  type RenderResult,
  type RenderSettings,
} from './sprite-render';

declare global {
  interface Window {
    renderSprites: (settings: RenderSettings) => Promise<RenderResult>;
  }
}

const COLOR_CELL_SCALE = 2;
const PIXEL_CELL_SCALE = 4;
/** The widest look sheet; a canvas of more pixels on a side does not draw. */
const SHEET_PIXELS = 16384;

function sheetCanvas(columns: number, rows: number, cellWidth: number, cellHeight: number): CanvasRenderingContext2D {
  const canvas = document.createElement('canvas');
  canvas.width = columns * cellWidth;
  canvas.height = rows * cellHeight;
  const context = canvas.getContext('2d');
  if (context === null) throw new Error('no 2D canvas context');
  context.imageSmoothingEnabled = false;
  return context;
}

function drawPixels(context: CanvasRenderingContext2D, rows: readonly string[], left: number, top: number, palette: readonly string[], scale: number): void {
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      const digit = row[x];
      if (digit === undefined || digit === '.') continue;
      context.fillStyle = palette[IMAGE_PIXELS.indexOf(digit)] ?? '#ff00ff';
      context.fillRect(left + x * scale, top + y * scale, scale, scale);
    }
  });
}

async function renderSprites(settings: RenderSettings): Promise<RenderResult> {
  const { width, height, anchorX, anchorY, bodyHeight, supersample } = settings;
  const palette = parsePalette(settings.palette);
  const gltf = await new GLTFLoader().loadAsync(settings.modelUrl);
  const model = gltf.scene;
  model.traverse((node) => {
    if (!(node instanceof Mesh)) return;
    node.frustumCulled = false;
    if (settings.metalness === undefined) return;
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (material instanceof MeshStandardMaterial) material.metalness *= settings.metalness;
    }
  });
  for (const url of settings.clipModelUrls ?? []) gltf.animations.push(...(await new GLTFLoader().loadAsync(url)).animations);

  const bounds = new Box3().setFromObject(model, true);
  const center = bounds.getCenter(new Vector3());
  const parts = settings.parts === undefined ? null : PartLayer.split(model, settings.parts, bounds.min.y);
  const decals = settings.decals === undefined ? null : DecalLayer.read(model, settings.decals);
  const wings = settings.wings === undefined ? null : WingLayer.read(model, settings.wings);
  const motion = settings.rotors ?? (wings === null ? undefined : { rotors: [] });
  const rotorGroups = motion === undefined ? null : RotorLayer.split(model, motion);
  const [originX, originZ] = settings.parts?.pivot ?? [center.x, center.z];
  model.position.set(-originX, -bounds.min.y, -originZ);
  const pivot = new Group();
  pivot.add(model);
  const rotors =
    motion === undefined || rotorGroups === null
      ? null
      : RotorLayer.mount(motion, rotorGroups, model, pivot, bounds.max.y - bounds.min.y);
  const usesPose = settings.clips.some((request) => request.pose !== undefined || request.controller === true);
  const poses = usesPose ? new PoseLayer(model, pivot) : null;
  const weapon = settings.weapon === undefined ? null : await WeaponLayer.load(model, settings.weapon, bounds.max.y - bounds.min.y);

  const scene = new Scene();
  scene.add(pivot);
  if (poses !== null) scene.add(poses.controller);
  if (weapon !== null) scene.add(weapon.object);
  scene.add(new HemisphereLight(0xffffff, 0x404040, 2 * settings.light));
  const sun = new DirectionalLight(0xffffff, 2.5 * settings.light);
  sun.position.set(-3, 5, 4);
  scene.add(sun);

  const pitch = (PITCH_DEGREES * Math.PI) / 180;
  const footprint = settings.footprint;
  const unitsPerPixel = footprint === undefined ? (bounds.max.y - bounds.min.y) * Math.cos(pitch) / bodyHeight : Math.SQRT2 / footprint.tileWidth;
  if (footprint !== undefined) {
    const size = bounds.getSize(new Vector3());
    const heightUnits = (footprint.height * footprint.tileHeight * unitsPerPixel) / Math.cos(pitch);
    model.scale.set(footprint.width / size.x, heightUnits / size.y, footprint.depth / size.z);
    model.position.set(-center.x * model.scale.x, -bounds.min.y * model.scale.y, -center.z * model.scale.z);
  }
  const camera = new OrthographicCamera(
    -anchorX * unitsPerPixel,
    (width - anchorX) * unitsPerPixel,
    anchorY * unitsPerPixel,
    -(height - anchorY) * unitsPerPixel,
    0.01,
    100,
  );
  camera.position.set(0, 10 * Math.sin(pitch), 10 * Math.cos(pitch));
  camera.lookAt(0, 0, 0);

  const renderer = new WebGLRenderer({ alpha: true, antialias: false, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(width * supersample, height * supersample);
  renderer.setClearColor(0x000000, 0);
  const readback = sheetCanvas(1, 1, width * supersample, height * supersample);

  const totalFrames = settings.clips.reduce((sum, request) => sum + request.frames, 0);
  const facingCount = settings.facings ?? FACINGS;
  const colorScale = Math.max(1, Math.min(COLOR_CELL_SCALE, Math.floor(SHEET_PIXELS / (totalFrames * width))));
  const pixelScale = Math.max(1, Math.min(PIXEL_CELL_SCALE, Math.floor(SHEET_PIXELS / (totalFrames * width))));
  const colorCellWidth = width * colorScale;
  const colorCellHeight = height * colorScale;
  const colorSheet = sheetCanvas(totalFrames, facingCount, colorCellWidth, colorCellHeight);
  const pixelSheet = sheetCanvas(totalFrames, facingCount, width * pixelScale, height * pixelScale);

  const mixer = new AnimationMixer(model);
  const animations: RenderedAnimation[] = [];
  let column = 0;
  const clipNamed = (name: string) => {
    const clip = gltf.animations.find((candidate) => candidate.name === name);
    if (clip === undefined) throw new Error(`the GLB has no clip ${name}; it has ${gltf.animations.map((c) => c.name).join(', ')}`);
    return clip;
  };
  /** Poses the rig: each action at its time and weight. */
  const pose = (layers: readonly { action: AnimationAction; time: number; weight: number }[]) => {
    mixer.stopAllAction();
    for (const layer of layers) {
      layer.action.reset().play();
      layer.action.setEffectiveWeight(layer.weight);
      layer.action.time = layer.time;
    }
    mixer.update(0);
  };
  for (const request of settings.clips) {
    const clip = clipNamed(request.clip);
    const action = mixer.clipAction(clip);
    action.setLoop(request.loop ? LoopRepeat : LoopOnce, Infinity);
    action.clampWhenFinished = true;
    const start = request.start ?? 0;
    const end = request.end ?? clip.duration;
    const from = request.from === undefined ? null : mixer.clipAction(clipNamed(request.from));
    let trunk = null;
    if (request.trunk !== undefined) {
      pose([{ action: mixer.clipAction(clipNamed(request.trunk.clip)), time: request.trunk.time, weight: 1 }]);
      trunk = readTrunk(model);
    }
    const times = from === null ? frameTimes(end - start, request.frames, request.loop).map((time) => start + time) : Array<number>(request.frames).fill(start);
    const facings: string[][][] = [];
    for (let viewFacing = 0; viewFacing < facingCount; viewFacing++) {
      pivot.rotation.y = facingYaw(viewFacing, settings.yawOffset);
      pivot.updateMatrixWorld(true);
      const frames: string[][] = [];
      times.forEach((time, frame) => {
        const weight = from === null ? 1 : (frame + 1) / request.frames;
        pose(from === null ? [{ action, time, weight: 1 }] : [{ action: from, time: 0, weight: 1 - weight }, { action, time, weight }]);
        if (trunk !== null) setTrunk(model, trunk);
        poses?.apply(request.pose, weight, request.controller === true && weight >= 0.5);
        parts?.apply(request);
        wings?.apply(request, frame, request.frames);
        rotors?.apply(request, frame, request.frames, unitsPerPixel, pitch, settings.yawOffset);
        weapon?.place(request.name ?? request.clip);
        decals?.hide();
        renderer.render(scene, camera);
        readback.clearRect(0, 0, readback.canvas.width, readback.canvas.height);
        readback.drawImage(renderer.domElement, 0, 0);
        const pixels = readback.getImageData(0, 0, readback.canvas.width, readback.canvas.height).data;
        colorSheet.drawImage(renderer.domElement, (column + frame) * colorCellWidth, viewFacing * colorCellHeight, colorCellWidth, colorCellHeight);
        const colors =
          weapon === null || settings.weapon === undefined
            ? undefined
            : { mask: weapon.mask(renderer, scene, camera, readback, model), body: settings.bodyColors, weapon: settings.weapon.colors };
        const rows = pixelate(pixels, width, height, supersample, palette, colors);
        decals?.paint(rows, request, renderer, scene, camera, readback, width, supersample);
        frames.push(rows);
        drawPixels(pixelSheet, rows, (column + frame) * width * pixelScale, viewFacing * height * pixelScale, settings.palette, pixelScale);
      });
      facings.push(frames);
    }
    const steps = request.loop ? request.frames : Math.max(request.frames - 1, 1);
    const frameMs = request.frameMs ?? Math.round(((end - start) * 1000) / steps);
    animations.push({ clip: request.name ?? request.clip, frameMs, loop: request.loop, facings });
    column += request.frames;
  }
  renderer.dispose();
  return {
    animations,
    colorSheet: colorSheet.canvas.toDataURL('image/png'),
    pixelSheet: pixelSheet.canvas.toDataURL('image/png'),
  };
}

window.renderSprites = renderSprites;
document.title = 'ready';
