// The rotors of a still drone model for the model sprite renderer: it cuts each rotor disc out of the model so that
// it turns about its own axis, and moves the whole model for a clip request: a tilt, a yaw turn, and one wave of
// pitch, roll and height over the clip.

import { BufferAttribute, Group, Mesh, Quaternion, Vector3, type Object3D } from 'three';
import type { ClipRequest, RotorSettings } from './sprite-render';

const DEGREES = Math.PI / 180;

/** The rotor groups of a split model, and the group between the pivot and the model that tilts and turns it. */
export class RotorLayer {
  private constructor(
    private readonly settings: RotorSettings,
    private readonly rotors: readonly Group[],
    private readonly motion: Group,
    private readonly middle: number,
  ) {}

  /**
   * Splits every mesh of a model whose root still stands at the origin: a triangle whose middle lies in a rotor disc
   * and is not in a keep box goes to the group of that rotor, which turns about the rotor axis.
   */
  static split(model: Object3D, settings: RotorSettings): Group[] {
    model.updateMatrixWorld(true);
    const groups = settings.rotors.map(({ center: [x, z] }) => {
      const group = new Group();
      group.position.set(x, 0, z);
      return group;
    });
    const meshes: Mesh[] = [];
    model.traverse((node) => {
      if (node instanceof Mesh) meshes.push(node);
    });
    const a = new Vector3();
    const b = new Vector3();
    const c = new Vector3();
    const kept = (middle: Vector3) =>
      (settings.keep ?? []).some(([x0 = 0, y0 = 0, z0 = 0, x1 = 0, y1 = 0, z1 = 0]) =>
        middle.x >= x0 && middle.x <= x1 && middle.y >= y0 && middle.y <= y1 && middle.z >= z0 && middle.z <= z1,
      );
    for (const mesh of meshes) {
      const geometry = mesh.geometry;
      const position = geometry.getAttribute('position');
      const index = geometry.getIndex();
      const count = index === null ? position.count : index.count;
      const vertex = (i: number) => (index === null ? i : index.getX(i));
      const bodyIndex: number[] = [];
      const rotorIndex: number[][] = settings.rotors.map(() => []);
      for (let i = 0; i < count; i += 3) {
        const corners = [vertex(i), vertex(i + 1), vertex(i + 2)];
        a.fromBufferAttribute(position, corners[0] ?? 0).applyMatrix4(mesh.matrixWorld);
        b.fromBufferAttribute(position, corners[1] ?? 0).applyMatrix4(mesh.matrixWorld);
        c.fromBufferAttribute(position, corners[2] ?? 0).applyMatrix4(mesh.matrixWorld);
        const middle = a.add(b).add(c).divideScalar(3);
        const rotor = kept(middle)
          ? -1
          : settings.rotors.findIndex(({ center: [x, z], radius, bottom }) => middle.y >= bottom && Math.hypot(middle.x - x, middle.z - z) <= radius);
        (rotor < 0 ? bodyIndex : (rotorIndex[rotor] ?? bodyIndex)).push(...corners);
      }
      rotorIndex.forEach((triangles, rotor) => {
        const group = groups[rotor];
        if (triangles.length === 0 || group === undefined) return;
        const part = new Mesh(geometry.clone(), mesh.material);
        part.geometry.setIndex(new BufferAttribute(new Uint32Array(triangles), 1));
        part.geometry.applyMatrix4(mesh.matrixWorld);
        part.geometry.translate(-group.position.x, 0, -group.position.z);
        part.frustumCulled = false;
        group.add(part);
      });
      geometry.setIndex(new BufferAttribute(new Uint32Array(bodyIndex), 1));
    }
    for (const group of groups) model.add(group);
    return groups;
  }

  /**
   * Puts a motion group between the pivot and the model, at the middle height of the model, which stands with its
   * bottom at the origin of the pivot.
   */
  static mount(settings: RotorSettings, rotors: readonly Group[], model: Object3D, pivot: Object3D, height: number): RotorLayer {
    const motion = new Group();
    const middle = height / 2;
    pivot.remove(model);
    pivot.add(motion);
    motion.add(model);
    model.position.y -= middle;
    return new RotorLayer(settings, rotors, motion, middle);
  }

  /**
   * Turns the rotors and moves the model for the frame `frame` of `frames` of a clip request. `unitsPerPixel` turns
   * the bob in game pixels into model units; `yawOffset` (degrees) turns the model front to the front of the pivot.
   */
  apply(request: ClipRequest, frame: number, frames: number, unitsPerPixel: number, pitch: number, yawOffset: number): void {
    const turn = (request.rotorTurn ?? 0) * frame * DEGREES;
    this.rotors.forEach((group, rotor) => {
      group.rotation.y = turn * (this.settings.rotors[rotor]?.spin ?? 1);
    });
    const phase = (2 * Math.PI * frame) / Math.max(frames, 1);
    const wave = request.wave;
    const nose = ((request.tilt?.[0] ?? 0) + (wave?.pitch ?? 0) * Math.sin(phase)) * DEGREES;
    const side = ((request.tilt?.[1] ?? 0) + (wave?.roll ?? 0) * Math.cos(phase)) * DEGREES;
    const yaw = ((request.turn ?? 0) * frame) / Math.max(frames, 1);
    const offset = yawOffset * DEGREES;
    const front = new Vector3(-Math.sin(offset), 0, Math.cos(offset));
    const pitchAxis = front.clone().cross(new Vector3(0, -1, 0)).normalize();
    const tilt = new Quaternion().setFromAxisAngle(pitchAxis, nose).premultiply(new Quaternion().setFromAxisAngle(front, side));
    this.motion.quaternion.setFromAxisAngle(new Vector3(0, 1, 0), yaw * DEGREES).multiply(tilt);
    this.motion.position.y = this.middle + ((wave?.bob ?? 0) * Math.sin(phase) * unitsPerPixel) / Math.cos(pitch);
  }
}
