// The decals of a model in the render page: the meshes of a named material, such as the lettering of a ship name.
// The color render hides them, and a mask render tells for each pixel whether a decal is in front, so the frame takes
// the decal palette color there.

import { DoubleSide, Mesh, MeshBasicMaterial, type Camera, type Material, type Object3D, type Scene, type WebGLRenderer } from 'three';
import { paintDecal, type ClipRequest, type DecalSettings } from './sprite-render';

const BODY_MASK = new MeshBasicMaterial({ color: 0x000000 });
const DECAL_MASK = new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide });

function materialsOf(mesh: Mesh): Material[] {
  return Array.isArray(mesh.material) ? mesh.material : [mesh.material];
}

export class DecalLayer {
  private constructor(private readonly decals: readonly { readonly settings: DecalSettings; readonly meshes: readonly Mesh[] }[]) {}

  /** Finds the meshes of each decal material; a decal with no mesh is an error. */
  static read(model: Object3D, settings: readonly DecalSettings[]): DecalLayer {
    const decals = settings.map((decal) => {
      const meshes: Mesh[] = [];
      model.traverse((node) => {
        if (node instanceof Mesh && materialsOf(node).some((material) => material.name === decal.material)) meshes.push(node);
      });
      if (meshes.length === 0) throw new Error(`the model has no mesh of the decal material ${decal.material}`);
      return { settings: decal, meshes };
    });
    return new DecalLayer(decals);
  }

  /** Hides every decal mesh for the color render. */
  hide(): void {
    for (const decal of this.decals) for (const mesh of decal.meshes) mesh.visible = false;
  }

  /** Paints each decal into the rows of the frame just rendered, unless the request is burnt. */
  paint(
    rows: string[],
    request: ClipRequest,
    renderer: WebGLRenderer,
    scene: Scene,
    camera: Camera,
    readback: CanvasRenderingContext2D,
    width: number,
    scale: number,
  ): void {
    if (request.burnt === true) return;
    const all: Mesh[] = [];
    scene.traverse((node) => {
      if (node instanceof Mesh && node.visible) all.push(node);
    });
    for (const decal of this.decals) {
      const saved = new Map<Mesh, { material: Material | Material[]; visible: boolean }>();
      for (const mesh of [...all, ...decal.meshes]) {
        saved.set(mesh, { material: mesh.material, visible: mesh.visible });
        mesh.material = BODY_MASK;
      }
      for (const mesh of decal.meshes) {
        mesh.material = DECAL_MASK;
        mesh.visible = true;
      }
      renderer.render(scene, camera);
      for (const [mesh, { material, visible }] of saved) {
        mesh.material = material;
        mesh.visible = visible;
      }
      readback.clearRect(0, 0, readback.canvas.width, readback.canvas.height);
      readback.drawImage(renderer.domElement, 0, 0);
      const mask = readback.getImageData(0, 0, readback.canvas.width, readback.canvas.height).data;
      paintDecal(rows, mask, width, scale, decal.settings.color);
    }
  }
}
