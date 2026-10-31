// Poses that the Tripo presets do not have, laid over a posed rig in the render page: a crouch, a sit on the ground,
// and a controller held in both hands. The bone names are those of a Tripo biped rig.

import { BoxGeometry, Mesh, MeshStandardMaterial, Quaternion, Vector3, type Object3D } from 'three';
import type { PoseName } from './sprite-render';

/** The drop of the hips in a crouch, as a part of the leg length. */
const CROUCH_DROP = 0.3;
/** The forward lean of the upper body in a crouch, in degrees. */
const CROUCH_LEAN = 18;
/** The height of the hip bone above the ground when the model sits on the ground, as a part of the leg length. */
const SIT_HIP_HEIGHT = 0.18;
/** The distance of the feet in front of the hips when the model sits on the ground, as a part of the leg length. */
const SIT_FEET_FORWARD = 0.62;
/** The size of the controller in model units: across the hands, front to back, and high. */
const CONTROLLER_SIZE = [0.13, 0.07, 0.035] as const;
const CONTROLLER_COLOR = 0x2a2a2a;

interface Leg {
  readonly thigh: Object3D;
  readonly calf: Object3D;
  readonly foot: Object3D;
}

function bone(model: Object3D, name: string): Object3D {
  const found = model.getObjectByName(name);
  if (found === undefined) throw new Error(`the rig has no bone ${name}`);
  return found;
}

function worldOf(node: Object3D): Vector3 {
  node.updateWorldMatrix(true, false);
  return node.getWorldPosition(new Vector3());
}

/** Applies a rotation given in world space to a bone, about its own origin. */
function turnInWorld(node: Object3D, turn: Quaternion): void {
  const parent = node.parent;
  const parentRotation = parent === null ? new Quaternion() : parent.getWorldQuaternion(new Quaternion());
  const local = parentRotation.clone().invert().multiply(turn).multiply(parentRotation);
  node.quaternion.premultiply(local);
  node.updateMatrixWorld(true);
}

/** Turns a bone so that the point `from` of its chain moves toward the world point `to`. */
function aim(node: Object3D, from: Vector3, to: Vector3): void {
  const origin = worldOf(node);
  const a = from.clone().sub(origin).normalize();
  const b = to.clone().sub(origin).normalize();
  turnInWorld(node, new Quaternion().setFromUnitVectors(a, b));
}

/** Moves a bone by a world offset. */
function shift(node: Object3D, offset: Vector3): void {
  const parent = node.parent;
  if (parent === null) {
    node.position.add(offset);
  } else {
    const target = worldOf(node).add(offset);
    node.position.copy(parent.worldToLocal(target));
  }
  node.updateMatrixWorld(true);
}

/** Bends a leg so that its foot reaches `target`, with the knee toward `hint`; the foot keeps its world rotation. */
function reach(leg: Leg, target: Vector3, hint: Vector3): void {
  const footRotation = leg.foot.getWorldQuaternion(new Quaternion());
  const hip = worldOf(leg.thigh);
  const knee = worldOf(leg.calf);
  const ankle = worldOf(leg.foot);
  const thighLength = hip.distanceTo(knee);
  const calfLength = knee.distanceTo(ankle);
  const toTarget = target.clone().sub(hip);
  const distance = Math.min(Math.max(toTarget.length(), Math.abs(thighLength - calfLength) + 1e-4), thighLength + calfLength - 1e-4);
  const along = toTarget.normalize();
  const a = (thighLength ** 2 - calfLength ** 2 + distance ** 2) / (2 * distance);
  const h = Math.sqrt(Math.max(0, thighLength ** 2 - a ** 2));
  const side = hint.clone().sub(along.clone().multiplyScalar(hint.dot(along))).normalize();
  const kneeTarget = hip.clone().add(along.clone().multiplyScalar(a)).add(side.multiplyScalar(h));
  aim(leg.thigh, knee, kneeTarget);
  aim(leg.calf, worldOf(leg.foot), hip.clone().add(along.multiplyScalar(distance)));
  const calfRotation = leg.calf.getWorldQuaternion(new Quaternion());
  leg.foot.quaternion.copy(calfRotation.invert().multiply(footRotation));
  leg.foot.updateMatrixWorld(true);
}

/** The hip, leg and spine bones of a Tripo biped rig. */
const TRUNK_BONE = /^(Root|Hip|Pelvis|Waist|Spine\d+|[LR]_(Thigh|Calf|Foot|ToeBase).*)$/;

/** The local position and turn of each hip, leg and spine bone of a posed rig. */
export type TrunkPose = ReadonlyMap<Object3D, { readonly position: Vector3; readonly turn: Quaternion }>;

/** Reads the hip, leg and spine bones of the rig as the animation posed them. */
export function readTrunk(model: Object3D): TrunkPose {
  const trunk = new Map<Object3D, { position: Vector3; turn: Quaternion }>();
  model.traverse((node) => {
    if (TRUNK_BONE.test(node.name)) trunk.set(node, { position: node.position.clone(), turn: node.quaternion.clone() });
  });
  return trunk;
}

/** Puts the hip, leg and spine bones of the rig back into a pose of `readTrunk`. */
export function setTrunk(model: Object3D, trunk: TrunkPose): void {
  for (const [node, { position, turn }] of trunk) {
    node.position.copy(position);
    node.quaternion.copy(turn);
  }
  model.updateMatrixWorld(true);
}

/** The pose layer of one model: call `apply` after the animation posed the rig for a frame. */
export class PoseLayer {
  private readonly hip: Object3D;
  private readonly waist: Object3D;
  private readonly legs: readonly Leg[];
  private readonly hands: readonly [Object3D, Object3D];
  private readonly legLength: number;
  private readonly ankleHeight: number;
  /** The front of the model in the space of its pivot, flat on the ground. */
  private readonly front: Vector3;
  readonly controller: Mesh;

  /** Reads the rig in its bind pose; the pivot must not be turned yet. */
  constructor(private readonly model: Object3D, private readonly pivot: Object3D) {
    this.hip = bone(model, 'Hip');
    this.waist = bone(model, 'Waist');
    this.legs = (['L', 'R'] as const).map((side) => ({
      thigh: bone(model, `${side}_Thigh`),
      calf: bone(model, `${side}_Calf`),
      foot: bone(model, `${side}_Foot`),
    }));
    this.hands = [bone(model, 'L_Hand'), bone(model, 'R_Hand')];
    const leg = this.legs[0] as Leg;
    this.legLength = worldOf(leg.thigh).distanceTo(worldOf(leg.calf)) + worldOf(leg.calf).distanceTo(worldOf(leg.foot));
    this.ankleHeight = worldOf(leg.foot).y - pivot.getWorldPosition(new Vector3()).y;
    const front = new Vector3();
    for (const side of ['L', 'R']) front.add(worldOf(bone(model, `${side}_ToeBase`)).sub(worldOf(bone(model, `${side}_Foot`))));
    this.front = front.setY(0).normalize();
    const [width, depth, height] = CONTROLLER_SIZE;
    this.controller = new Mesh(new BoxGeometry(width, height, depth), new MeshStandardMaterial({ color: CONTROLLER_COLOR }));
    this.controller.visible = false;
  }

  private frontNow(): Vector3 {
    return this.front.clone().applyQuaternion(this.pivot.getWorldQuaternion(new Quaternion()));
  }

  /** Lays a pose over the animated rig with a weight from 0 to 1, and shows the controller in the hands or hides it. */
  apply(pose: PoseName | undefined, weight: number, controller: boolean): void {
    this.model.updateMatrixWorld(true);
    if (pose === 'crouch') this.crouch(weight);
    if (pose === 'groundSit') this.sit(weight);
    this.placeController(controller);
  }

  private crouch(weight: number): void {
    const feet = this.legs.map((leg) => worldOf(leg.foot));
    const front = this.frontNow();
    shift(this.hip, new Vector3(0, -CROUCH_DROP * this.legLength * weight, 0));
    const axis = new Vector3(0, 1, 0).cross(front).normalize();
    turnInWorld(this.waist, new Quaternion().setFromAxisAngle(axis, (CROUCH_LEAN * Math.PI * weight) / 180));
    this.legs.forEach((leg, index) => reach(leg, feet[index] as Vector3, front));
  }

  private sit(weight: number): void {
    const feet = this.legs.map((leg) => worldOf(leg.foot));
    const front = this.frontNow();
    const ground = this.pivot.getWorldPosition(new Vector3()).y;
    const hipY = worldOf(this.hip).y;
    shift(this.hip, new Vector3(0, (ground + SIT_HIP_HEIGHT * this.legLength - hipY) * weight, 0));
    this.legs.forEach((leg, index) => {
      const hip = worldOf(leg.thigh);
      const onGround = hip.clone().add(front.clone().multiplyScalar(SIT_FEET_FORWARD * this.legLength)).setY(ground + this.ankleHeight);
      const target = (feet[index] as Vector3).clone().lerp(onGround, weight);
      reach(leg, target, front.clone().add(new Vector3(0, 1.5, 0)));
    });
  }

  /** Puts the controller between the two hands, a little in front of the wrists, turned with the line of the hands. */
  private placeController(visible: boolean): void {
    this.controller.visible = visible;
    if (!visible) return;
    const [left, right] = this.hands.map(worldOf) as [Vector3, Vector3];
    const across = right.clone().sub(left).normalize();
    const middle = left.clone().add(right).multiplyScalar(0.5).add(this.frontNow().multiplyScalar(0.03));
    this.controller.position.copy(middle);
    this.controller.quaternion.setFromUnitVectors(new Vector3(1, 0, 0), across);
    this.controller.updateMatrixWorld(true);
  }
}
