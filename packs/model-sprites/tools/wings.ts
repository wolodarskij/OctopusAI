// The wings of a still bird model for the model sprite renderer: it bends each wing about a hinge along the body for a
// wing beat. The bend grows smoothly over the shoulder, and the outer part of the wing bends more, a little later in
// the beat, so the wing curves as it beats.

import { Matrix4, Mesh, Vector3, type BufferAttribute, type Object3D } from 'three';
import type { ClipRequest, WingSettings } from './sprite-render';

const DEGREES = Math.PI / 180;

/** The part of `value` between `from` and `from + width`, eased: 0 before, 1 after. */
function smoothstep(from: number, width: number, value: number): number {
  const t = Math.min(Math.max((value - from) / Math.max(width, 1e-9), 0), 1);
  return t * t * (3 - 2 * t);
}

/** One mesh of the model, with its rest positions and normals in the axes of the model. */
interface WingMesh {
  readonly position: BufferAttribute;
  readonly normal: BufferAttribute | null;
  readonly rest: Float32Array;
  readonly restNormal: Float32Array | null;
  readonly toMesh: Matrix4;
  readonly toMeshNormal: Matrix4;
}

/** The wings of a model: call `apply` for each frame of a clip request. */
export class WingLayer {
  private constructor(
    private readonly settings: WingSettings,
    private readonly meshes: readonly WingMesh[],
  ) {}

  /** Reads the rest shape of every mesh of a model, in the axes of the model root. */
  static read(model: Object3D, settings: WingSettings): WingLayer {
    model.updateMatrixWorld(true);
    const toModel = new Matrix4().copy(model.matrixWorld).invert();
    const meshes: WingMesh[] = [];
    model.traverse((node) => {
      if (!(node instanceof Mesh)) return;
      const geometry = node.geometry;
      const position = geometry.getAttribute('position') as BufferAttribute;
      const normalAttribute = geometry.getAttribute('normal') as BufferAttribute | undefined;
      const normal = normalAttribute ?? null;
      const meshToModel = new Matrix4().multiplyMatrices(toModel, node.matrixWorld);
      const normalToModel = new Matrix4().extractRotation(meshToModel);
      const rest = new Float32Array(position.count * 3);
      const restNormal = normal === null ? null : new Float32Array(normal.count * 3);
      const point = new Vector3();
      for (let index = 0; index < position.count; index++) {
        point.fromBufferAttribute(position, index).applyMatrix4(meshToModel).toArray(rest, index * 3);
        if (normal !== null && restNormal !== null) point.fromBufferAttribute(normal, index).applyMatrix4(normalToModel).normalize().toArray(restNormal, index * 3);
      }
      meshes.push({
        position,
        normal,
        rest,
        restNormal,
        toMesh: meshToModel.clone().invert(),
        toMeshNormal: normalToModel.clone().invert(),
      });
    });
    return new WingLayer(settings, meshes);
  }

  /**
   * Bends the wings for the frame `frame` of `frames` of a clip request. Frame 0 shows the wings at the top of the
   * beat; a request with no `wingBeat` holds them flat.
   */
  apply(request: ClipRequest, frame: number, frames: number): void {
    const beat = request.wingBeat;
    const phase = (2 * Math.PI * frame) / Math.max(frames, 1);
    const base = beat === undefined ? 0 : (beat.middle + beat.amplitude * Math.cos(phase)) * DEGREES;
    const tip = beat === undefined ? 0 : beat.tip * Math.cos(phase - beat.lag * DEGREES) * DEGREES;
    const { span, middle, shoulder, height, blend, elbow } = this.settings;
    const spanIndex = span === 'x' ? 0 : 2;
    const point = new Vector3();
    for (const mesh of this.meshes) {
      const bent = new Float32Array(mesh.rest);
      const bentNormal = mesh.restNormal === null ? null : new Float32Array(mesh.restNormal);
      for (let index = 0; index < mesh.position.count; index++) {
        const at = index * 3;
        const across = (mesh.rest[at + spanIndex] ?? 0) - middle;
        const side = across < 0 ? -1 : 1;
        const out = side * across - shoulder;
        if (out <= 0) continue;
        const angle = smoothstep(0, blend, out) * base + smoothstep(elbow - shoulder, blend, out) * tip;
        const cos = Math.cos(angle);
        const sin = Math.sin(angle);
        const v = (mesh.rest[at + 1] ?? 0) - height;
        bent[at + spanIndex] = middle + side * (shoulder + out * cos - v * sin);
        bent[at + 1] = height + out * sin + v * cos;
        if (bentNormal !== null && mesh.restNormal !== null) {
          const nu = side * (mesh.restNormal[at + spanIndex] ?? 0);
          const nv = mesh.restNormal[at + 1] ?? 0;
          bentNormal[at + spanIndex] = side * (nu * cos - nv * sin);
          bentNormal[at + 1] = nu * sin + nv * cos;
        }
      }
      for (let index = 0; index < mesh.position.count; index++) {
        point.fromArray(bent, index * 3).applyMatrix4(mesh.toMesh);
        mesh.position.setXYZ(index, point.x, point.y, point.z);
        if (mesh.normal !== null && bentNormal !== null) {
          point.fromArray(bentNormal, index * 3).applyMatrix4(mesh.toMeshNormal).normalize();
          mesh.normal.setXYZ(index, point.x, point.y, point.z);
        }
      }
      mesh.position.needsUpdate = true;
      if (mesh.normal !== null) mesh.normal.needsUpdate = true;
    }
  }
}
