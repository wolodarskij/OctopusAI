// The parts of a still vehicle model for the model sprite renderer: it splits the model into a hull and a turret by
// boxes, turns the turret about its own axis, and shows one part for each clip request, whole or burnt, with the
// turret on the hull or thrown to the ground.

import { BufferAttribute, Color, DoubleSide, Group, Mesh, Vector3, type Material, type Object3D } from 'three';
import type { ClipRequest, PartSettings } from './sprite-render';

const DEGREES = Math.PI / 180;

function colorOf(material: Material): Color | null {
  return 'color' in material && material.color instanceof Color ? material.color : null;
}

/** The hull and the turret meshes of a split model. Both sides of each face show, so the turret ring of a hull is not a hole. */
export class PartLayer {
  private readonly colors = new Map<Color, Color>();
  private readonly turretBottom: number;

  private constructor(
    private readonly settings: PartSettings,
    private readonly hull: readonly Mesh[],
    private readonly turret: Group,
    private readonly ground: number,
  ) {
    for (const mesh of [...hull, ...turret.children]) {
      if (!(mesh instanceof Mesh)) continue;
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        (material as Material).side = DoubleSide;
        const color = colorOf(material as Material);
        if (color !== null && !this.colors.has(color)) this.colors.set(color, color.clone());
      }
    }
    let bottom = Infinity;
    turret.traverse((node) => {
      if (!(node instanceof Mesh)) return;
      const position = node.geometry.getAttribute('position');
      const index = node.geometry.getIndex();
      if (index === null) return;
      for (let i = 0; i < index.count; i++) bottom = Math.min(bottom, position.getY(index.getX(i)));
    });
    this.turretBottom = Number.isFinite(bottom) ? bottom : ground;
  }

  /**
   * Splits every mesh of a model whose root still stands at the origin: a triangle whose middle lies in a turret box
   * goes to the turret group, which turns about the pivot; the rest stays in the hull.
   */
  static split(model: Object3D, settings: PartSettings, ground: number): PartLayer {
    model.updateMatrixWorld(true);
    const [px, pz] = settings.pivot;
    const turret = new Group();
    turret.position.set(px, 0, pz);
    const hull: Mesh[] = [];
    const meshes: Mesh[] = [];
    model.traverse((node) => {
      if (node instanceof Mesh) meshes.push(node);
    });
    const a = new Vector3();
    const b = new Vector3();
    const c = new Vector3();
    for (const mesh of meshes) {
      const geometry = mesh.geometry;
      const position = geometry.getAttribute('position');
      const index = geometry.getIndex();
      const count = index === null ? position.count : index.count;
      const vertex = (i: number) => (index === null ? i : index.getX(i));
      const hullIndex: number[] = [];
      const turretIndex: number[] = [];
      for (let i = 0; i < count; i += 3) {
        const corners = [vertex(i), vertex(i + 1), vertex(i + 2)];
        a.fromBufferAttribute(position, corners[0] ?? 0).applyMatrix4(mesh.matrixWorld);
        b.fromBufferAttribute(position, corners[1] ?? 0).applyMatrix4(mesh.matrixWorld);
        c.fromBufferAttribute(position, corners[2] ?? 0).applyMatrix4(mesh.matrixWorld);
        const middle = a.add(b).add(c).divideScalar(3);
        const inTurret = settings.turret.some(([x0 = 0, y0 = 0, z0 = 0, x1 = 0, y1 = 0, z1 = 0]) =>
          middle.x >= x0 && middle.x <= x1 && middle.y >= y0 && middle.y <= y1 && middle.z >= z0 && middle.z <= z1,
        );
        (inTurret ? turretIndex : hullIndex).push(...corners);
      }
      if (turretIndex.length > 0) {
        const part = new Mesh(geometry.clone(), mesh.material);
        part.geometry.setIndex(new BufferAttribute(new Uint32Array(turretIndex), 1));
        part.geometry.applyMatrix4(mesh.matrixWorld);
        part.geometry.translate(-px, 0, -pz);
        part.frustumCulled = false;
        turret.add(part);
      }
      geometry.setIndex(new BufferAttribute(new Uint32Array(hullIndex), 1));
      hull.push(mesh);
    }
    model.add(turret);
    return new PartLayer(settings, hull, turret, ground);
  }

  /** Shows the part of the settings for a clip request: burnt or not, the turret on the hull or thrown. */
  apply(request: ClipRequest): void {
    const { part, burnt } = this.settings;
    for (const mesh of this.hull) mesh.visible = part !== 'turret';
    this.turret.visible = part !== 'hull';
    for (const [color, original] of this.colors) {
      color.copy(original);
      if (request.burnt === true) color.multiply(new Color(...burnt));
    }
    this.turret.rotation.set(0, (this.settings.turretYaw ?? 0) * DEGREES, 0);
    this.turret.position.y = 0;
    if (request.thrown === true) {
      this.turret.rotation.x = (this.settings.thrownTilt ?? 0) * DEGREES;
      this.turret.position.y = this.ground - this.turretBottom;
    }
  }
}
