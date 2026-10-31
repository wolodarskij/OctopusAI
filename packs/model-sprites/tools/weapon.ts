// A weapon model held in a hand bone of the posed rig in the render page, and the mask render that tells, for each
// pixel, whether the weapon or the body is in front.

import {
  Box3,
  Euler,
  Group,
  Matrix4,
  Mesh,
  MeshBasicMaterial,
  Object3D,
  Quaternion,
  Vector3,
  type Camera,
  type Material,
  type Scene,
  type WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import type { WeaponHold, WeaponSettings } from './sprite-render';

const DEGREES = Math.PI / 180;
const BODY_MASK = new MeshBasicMaterial({ color: 0x000000 });
const WEAPON_MASK = new MeshBasicMaterial({ color: 0xffffff });

function turnOf(degrees: readonly [number, number, number]): Quaternion {
  return new Quaternion().setFromEuler(new Euler(degrees[0] * DEGREES, degrees[1] * DEGREES, degrees[2] * DEGREES));
}

interface Hold {
  readonly body: boolean;
  readonly offset: Vector3;
  readonly turn: Quaternion;
}

function meshes(root: Object3D): Mesh[] {
  const found: Mesh[] = [];
  root.traverse((node) => {
    if (node instanceof Mesh) found.push(node);
  });
  return found;
}

/** A symmetric 3 x 3 matrix, row by row, times a vector. */
function times(matrix: readonly number[], v: Vector3): Vector3 {
  const [a = 0, b = 0, c = 0, d = 0, e = 0, f = 0, g = 0, h = 0, i = 0] = matrix;
  return new Vector3(a * v.x + b * v.y + c * v.z, d * v.x + e * v.y + f * v.z, g * v.x + h * v.y + i * v.z);
}

/** The unit vector that a symmetric 3 x 3 matrix stretches most, by power iteration. */
function largestAxis(matrix: readonly number[]): Vector3 {
  let axis = new Vector3(1, 0.7, 0.4).normalize();
  for (let step = 0; step < 100; step++) axis = times(matrix, axis).normalize();
  return axis;
}

/**
 * The turn that lays the main axes of the vertices of a model along x, y and z: its longest extent along x, the next
 * along y. Tripo makes a weapon lying at a slant, so its own axes say nothing.
 */
function mainAxesTurn(root: Object3D): Quaternion {
  root.updateMatrixWorld(true);
  const points: Vector3[] = [];
  for (const mesh of meshes(root)) {
    const position = mesh.geometry.getAttribute('position');
    for (let index = 0; index < position.count; index++) points.push(new Vector3().fromBufferAttribute(position, index).applyMatrix4(mesh.matrixWorld));
  }
  const mean = points.reduce((sum, point) => sum.add(point), new Vector3()).divideScalar(Math.max(points.length, 1));
  const covariance = new Array<number>(9).fill(0);
  for (const point of points) {
    const { x, y, z } = point.clone().sub(mean);
    [x * x, x * y, x * z, y * x, y * y, y * z, z * x, z * y, z * z].forEach((value, index) => {
      covariance[index] = (covariance[index] ?? 0) + value;
    });
  }
  const first = largestAxis(covariance);
  const stretch = first.dot(times(covariance, first));
  const outer = first.toArray();
  const rest = covariance.map((value, index) => value - stretch * (outer[Math.floor(index / 3)] ?? 0) * (outer[index % 3] ?? 0));
  const second = largestAxis(rest);
  second.sub(first.clone().multiplyScalar(second.dot(first))).normalize();
  const third = first.clone().cross(second);
  return new Quaternion().setFromRotationMatrix(new Matrix4().makeBasis(first, second, third)).invert();
}

/** The weapon of one render: call `place` after the rig is posed for a frame. */
export class WeaponLayer {
  /** The weapon in world space; add it to the scene. */
  readonly object = new Group();
  private readonly hand: Object3D;
  private readonly hold: Hold;
  private readonly holds = new Map<string, Hold>();

  private constructor(private readonly model: Object3D, weapon: Object3D, settings: WeaponSettings, bodyHeight: number) {
    const hand = model.getObjectByName(settings.bone);
    if (hand === undefined) throw new Error(`the rig has no bone ${settings.bone}`);
    this.hand = hand;

    const laid = new Group();
    laid.quaternion.copy(mainAxesTurn(weapon));
    laid.add(weapon);
    const holder = new Group();
    holder.add(laid);
    holder.updateMatrixWorld(true);
    const bounds = new Box3().setFromObject(holder, true);
    const size = bounds.getSize(new Vector3());
    const longest = Math.max(size.x, size.y, size.z);
    const scale = (settings.length * bodyHeight) / longest;
    const grip = bounds.getCenter(new Vector3()).add(new Vector3(...settings.grip).multiplyScalar(longest));
    const normal = new Group();
    normal.quaternion.copy(turnOf(settings.modelRotation));
    holder.position.copy(grip.negate());
    normal.add(holder);
    const thickness = settings.thickness ?? 1;
    normal.scale.set(scale, scale * thickness, scale * thickness);
    this.object.add(normal);
    for (const mesh of meshes(this.object)) mesh.frustumCulled = false;

    const holdOf = (hold: WeaponHold): Hold => ({
      body: hold.frame === 'body',
      offset: new Vector3(...hold.offset).multiplyScalar(bodyHeight),
      turn: turnOf(hold.rotation),
    });
    this.hold = holdOf({ frame: 'hand', offset: settings.offset, rotation: settings.rotation });
    for (const [animation, hold] of Object.entries(settings.holds ?? {})) this.holds.set(animation, holdOf(hold));
  }

  /** Loads the weapon model; `bodyHeight` is the height of the standing rig in world units. */
  static async load(model: Object3D, settings: WeaponSettings, bodyHeight: number): Promise<WeaponLayer> {
    const gltf = await new GLTFLoader().loadAsync(settings.modelUrl);
    return new WeaponLayer(model, gltf.scene, settings, bodyHeight);
  }

  /** Puts the grip of the weapon at the hand bone, turned with the hand or the body, as the hold of the animation says. */
  place(animation: string): void {
    const hold = this.holds.get(animation) ?? this.hold;
    this.hand.updateWorldMatrix(true, false);
    const position = this.hand.getWorldPosition(new Vector3());
    const rotation = (hold.body ? this.model : this.hand).getWorldQuaternion(new Quaternion());
    this.object.position.copy(position.add(hold.offset.clone().applyQuaternion(rotation)));
    this.object.quaternion.copy(rotation.multiply(hold.turn));
    this.object.updateMatrixWorld(true);
  }

  /**
   * Renders the scene once more with the body black and the weapon white, and gives the RGBA pixels: a white pixel
   * shows the weapon in front.
   */
  mask(renderer: WebGLRenderer, scene: Scene, camera: Camera, readback: CanvasRenderingContext2D, body: Object3D): Uint8ClampedArray {
    const saved = new Map<Mesh, Material | Material[]>();
    for (const mesh of meshes(body)) {
      saved.set(mesh, mesh.material);
      mesh.material = BODY_MASK;
    }
    for (const mesh of meshes(this.object)) {
      saved.set(mesh, mesh.material);
      mesh.material = WEAPON_MASK;
    }
    renderer.render(scene, camera);
    for (const [mesh, material] of saved) mesh.material = material;
    readback.clearRect(0, 0, readback.canvas.width, readback.canvas.height);
    readback.drawImage(renderer.domElement, 0, 0);
    return readback.getImageData(0, 0, readback.canvas.width, readback.canvas.height).data;
  }
}
