// The shared part of the model sprite renderer: the render settings, the result shape, the camera geometry of the
// view and the reduction of a rendered frame to a palette pixel image. The browser page and the Node driver both
// import it, so it uses no DOM and no Node API.

import { IMAGE_PIXELS } from './pixels';

/** The settings of one render. */
export interface RenderSettings {
  /** The URL of the animated GLB file. */
  readonly modelUrl: string;
  /** The size of one sprite frame, in game pixels. */
  readonly width: number;
  readonly height: number;
  /** The game pixel of the model origin (the feet) inside the frame. */
  readonly anchorX: number;
  readonly anchorY: number;
  /** The height of the standing model on screen, in game pixels. */
  readonly bodyHeight: number;
  /** Each game pixel is rendered as supersample x supersample pixels, then reduced by a box filter. */
  readonly supersample: number;
  /** A turn added to every facing, in degrees, for a model whose front is not +Z: -90 for the +X front of a Tripo rig. */
  readonly yawOffset: number;
  /** A factor on the light intensities: above 1 for a bright model whose shaded color would match a darker palette color. */
  readonly light: number;
  /** A factor on the metalness of every material, for a metal model that the render shows near black with no environment light. */
  readonly metalness?: number;
  /** The clips to render, by the clip name in the GLB, with the number of frames of each. */
  readonly clips: readonly ClipRequest[];
  /** The colors of the palette file, as `#rrggbb`, in index order. */
  readonly palette: readonly string[];
  /** The URLs of more GLB files of the same rig, whose clips the render can also use. */
  readonly clipModelUrls?: readonly string[];
  /** A weapon in a hand of the model. */
  readonly weapon?: WeaponSettings;
  /** The palette indices that a body pixel can take; every index when absent. */
  readonly bodyColors?: readonly number[];
  /** A still vehicle model in parts: the turret pivot is the anchor, and the render shows one part. */
  readonly parts?: PartSettings;
  /** A building: the model fills a footprint of tiles in the projection of the view, in place of `bodyHeight`. */
  readonly footprint?: FootprintSettings;
  /** A still drone model whose rotors turn; the clip requests can also tilt, turn and move it. */
  readonly rotors?: RotorSettings;
  /** A still bird model whose wings beat; the clip requests can also tilt and move it, as a drone. */
  readonly wings?: WingSettings;
  /** The view facings to render, from view facing 0; all 8 when absent. */
  readonly facings?: number;
  /** Lettering on the model, such as a ship name, drawn in one palette color so that it stays readable. */
  readonly decals?: readonly DecalSettings[];
}

/**
 * The meshes of one material of the model, drawn as one palette color: a sprite pixel that they cover by half or more
 * takes the color. The color render does not show them. A burnt request does not show them.
 */
export interface DecalSettings {
  /** The material name in the GLB. */
  readonly material: string;
  /** The palette index. */
  readonly color: number;
}

/** The rotors of a still drone model, in the units and the axes of the model file. */
export interface RotorSettings {
  readonly rotors: readonly Rotor[];
  /** Boxes [x0, y0, z0, x1, y1, z1]: a triangle whose middle lies in a box never turns, such as the top of the body. */
  readonly keep?: readonly (readonly number[])[];
}

/** One rotor: a triangle whose middle lies in its disc turns with it. */
export interface Rotor {
  /** The turn axis, [x, z]. */
  readonly center: readonly [number, number];
  readonly radius: number;
  /** The lowest height of the disc. */
  readonly bottom: number;
  /** 1 or -1: the turn direction. */
  readonly spin: number;
}

/**
 * The wings of a still bird model, in the units and the axes of the model file. Each wing turns about a hinge along
 * the body, at `shoulder` from the middle line and at `height`; the turn grows from none to full over `blend`.
 */
export interface WingSettings {
  /** The axis along which the wings reach: `z` for a model whose front is +X. */
  readonly span: 'x' | 'z';
  /** The middle line of the body on the span axis. */
  readonly middle: number;
  readonly shoulder: number;
  readonly height: number;
  readonly blend: number;
  /** The distance from the middle line where the extra turn of the wing tip starts. */
  readonly elbow: number;
}

/**
 * One wing beat over a clip, in degrees, up positive: the wings turn `middle + amplitude * cos(phase)`, and the tips
 * turn `tip * cos(phase - lag)` more. Frame 0 is the top of the beat.
 */
export interface WingBeat {
  readonly middle: number;
  readonly amplitude: number;
  readonly tip: number;
  readonly lag: number;
}

/** One wave over a clip: the amplitudes of the pitch and the roll in degrees, and of the height in game pixels. */
export interface MotionWave {
  readonly pitch: number;
  readonly roll: number;
  readonly bob: number;
}

/**
 * The footprint of a building model. The model x axis is the width and the model z axis, the front, is the depth.
 * The anchor of the frame is the middle of the footprint on the ground.
 */
export interface FootprintSettings {
  /** The size of the footprint in tiles. */
  readonly width: number;
  readonly depth: number;
  /** The height of the building in tiles. */
  readonly height: number;
  /** The size of one tile on screen, in the pixels of `width`: its diamond width, and the pixels of one tile of height. */
  readonly tileWidth: number;
  readonly tileHeight: number;
}

/** A weapon model that the render page puts in a hand bone of the rig. */
export interface WeaponSettings {
  /** The URL of the weapon GLB file. */
  readonly modelUrl: string;
  /** The name of the bone that holds the weapon, such as `R_Hand`. */
  readonly bone: string;
  /** The longest size of the weapon, as a part of the height of the standing model. */
  readonly length: number;
  /** A factor on the breadth and the height of the weapon, so that a thin weapon still shows at the sprite size. */
  readonly thickness?: number;
  /** A turn of the weapon model, in degrees about x, y and z, that points its barrel or its blade along +X and its top +Y. */
  readonly modelRotation: readonly [number, number, number];
  /** The point of the weapon that sits in the hand, from the middle of the turned model, in weapon lengths. */
  readonly grip: readonly [number, number, number];
  /** The place of the grip from the bone, in the axes of the bone, as a part of the height of the standing model. */
  readonly offset: readonly [number, number, number];
  /** The turn of the weapon in the axes of the bone, in degrees about x, y and z. */
  readonly rotation: readonly [number, number, number];
  /**
   * Another hold for an animation, by its output name: `body` turns the weapon in the axes of the model, whose front
   * is +X and whose top is +Y, so the weapon does not turn with the hand; the grip stays at the bone.
   */
  readonly holds?: Readonly<Record<string, WeaponHold>>;
  /**
   * The palette indices that a weapon pixel can take. Keep the uniform colors of the body out of it, so that a recolor
   * of the uniform does not change the weapon.
   */
  readonly colors: readonly number[];
}

export interface WeaponHold {
  readonly frame: 'hand' | 'body';
  /** The place of the grip from the bone, in the axes of the frame, as a part of the height of the standing model. */
  readonly offset: readonly [number, number, number];
  /** The turn of the weapon in the axes of the frame, in degrees about x, y and z. */
  readonly rotation: readonly [number, number, number];
}

/** A pose that the render page lays over a clip: a crouch, or a sit on the ground in place of a sit on a chair. */
export type PoseName = 'crouch' | 'groundSit';

export interface ClipRequest {
  readonly clip: string;
  readonly frames: number;
  /** True for a cycle such as a walk: the last frame stops one step before the start. False holds the last pose. */
  readonly loop: boolean;
  /** The name of the animation in the output; the clip name when absent. */
  readonly name?: string;
  /** The part of the clip to render, in seconds; the whole clip when absent. */
  readonly start?: number;
  readonly end?: number;
  readonly pose?: PoseName;
  /** True to show a controller in both hands. */
  readonly controller?: boolean;
  /**
   * A transition: the frames blend from the first pose of this clip to the first pose of `clip`, with the pose and
   * the controller of the request fading in. `frameMs` gives the frame time.
   */
  readonly from?: string;
  /** The frame time in milliseconds; the clip time divided by the frames when absent. */
  readonly frameMs?: number;
  /** The hips, the legs and the spine hold the pose of the clip `clip` at `time` seconds: the arms and the head of a clip on another body. */
  readonly trunk?: { readonly clip: string; readonly time: number };
  /** True to multiply the material colors of a model in parts by the burnt factor, for a wreck. */
  readonly burnt?: boolean;
  /** True to put the turret of a model in parts on the ground, tilted, as the wreck throws it. */
  readonly thrown?: boolean;
  /** The turn of each rotor from one frame to the next, in degrees. */
  readonly rotorTurn?: number;
  /** The tilt of a drone model, [nose down, right side down], in degrees. */
  readonly tilt?: readonly [number, number];
  /** The yaw turn of a drone model over the clip, in degrees; the last frame stops one step before the end. */
  readonly turn?: number;
  /** One wave of pitch, roll and height of a drone model over the clip. */
  readonly wave?: MotionWave;
  /** The wing beat of a bird model over the clip. */
  readonly wingBeat?: WingBeat;
}

/**
 * The parts of a still vehicle model, in the units and the axes of the model file. The scale of the render comes from
 * the whole model, and the turret pivot stands on the frame anchor, so the hull and the turret of one vehicle line up
 * at any pair of facings.
 */
export interface PartSettings {
  /** The part to render. */
  readonly part: 'all' | 'hull' | 'turret';
  /** The turn axis of the turret, [x, z]; the middle of the model for a vehicle with no turret. */
  readonly pivot: readonly [number, number];
  /** Boxes [x0, y0, z0, x1, y1, z1]: a triangle whose middle lies in a box belongs to the turret. */
  readonly turret: readonly (readonly number[])[];
  /** A turn of the turret about its axis, in degrees, that points its gun to the front of the hull. */
  readonly turretYaw?: number;
  /** The tilt of a thrown turret, in degrees. */
  readonly thrownTilt?: number;
  /** The factor on the material colors of a burnt request, [r, g, b]. */
  readonly burnt: readonly [number, number, number];
}

/** One rendered animation: the frame time, and the pixel rows of each frame for each of the 8 view facings. */
export interface RenderedAnimation {
  readonly clip: string;
  readonly frameMs: number;
  readonly loop: boolean;
  /** `facings[viewFacing][frame]` is one image: one string per pixel row, `.` transparent, a character of IMAGE_PIXELS a palette index. */
  readonly facings: readonly (readonly (readonly string[])[])[];
}

export interface RenderResult {
  readonly animations: readonly RenderedAnimation[];
  /** PNG data URLs for a look at the result: the full color renders and the palette sprites, one row per facing. */
  readonly colorSheet: string;
  readonly pixelSheet: string;
}

export const FACINGS = 8;

/** The camera pitch of the 2:1 dimetric view: sin 30 degrees is 0.5, so the ground is drawn at half height. */
export const PITCH_DEGREES = 30;

/**
 * The turn of the model about the vertical axis, in radians, that points a model whose front is +Z toward the screen
 * direction of a view facing. The camera looks along -Z, so +Z is screen down (view facing 1) and +X is screen right
 * (view facing 7); view facing 0 is screen down right.
 */
export function facingYaw(viewFacing: number, yawOffsetDegrees: number): number {
  return ((1 - viewFacing) * Math.PI) / 4 + (yawOffsetDegrees * Math.PI) / 180;
}

/** The time of each frame of a clip, in seconds. */
export function frameTimes(duration: number, frames: number, loop: boolean): number[] {
  if (frames <= 1) return [0];
  const step = loop ? duration / frames : duration / (frames - 1);
  return Array.from({ length: frames }, (_, frame) => Math.min(frame * step, duration));
}

export function parsePalette(colors: readonly string[]): [number, number, number][] {
  return colors.map((hex) => {
    const value = parseInt(hex.slice(1), 16);
    return [(value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
  });
}

/** The index of the palette color nearest to a color, by the squared distance in RGB, among the `allowed` indices. */
export function nearestColor(
  palette: readonly (readonly [number, number, number])[],
  r: number,
  g: number,
  b: number,
  allowed?: readonly number[],
): number {
  let best = 0;
  let bestDistance = Infinity;
  palette.forEach(([pr, pg, pb], index) => {
    if (allowed !== undefined && !allowed.includes(index)) return;
    const distance = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2;
    if (distance < bestDistance) {
      best = index;
      bestDistance = distance;
    }
  });
  return best;
}

/**
 * Reduces an RGBA image of (width x scale) x (height x scale) pixels, row by row from the top, to width x height
 * palette pixels. Each block of scale x scale pixels averages its alpha; a block with an average alpha of 128 or more
 * takes the nearest palette color of the average color of its visible pixels, and the rest is transparent.
 *
 * With `colors`, the white pixels of the `mask` image of the same size are the weapon: a block that shows more weapon
 * than body takes a weapon color, and any other block a body color.
 */
export function pixelate(
  rgba: ArrayLike<number>,
  width: number,
  height: number,
  scale: number,
  palette: readonly (readonly [number, number, number])[],
  colors?: { readonly mask: ArrayLike<number>; readonly body?: readonly number[]; readonly weapon: readonly number[] },
): string[] {
  const rows: string[] = [];
  const stride = width * scale;
  for (let y = 0; y < height; y++) {
    let row = '';
    for (let x = 0; x < width; x++) {
      let alpha = 0;
      let r = 0;
      let g = 0;
      let b = 0;
      let weapon = 0;
      for (let sy = 0; sy < scale; sy++) {
        for (let sx = 0; sx < scale; sx++) {
          const at = ((y * scale + sy) * stride + x * scale + sx) * 4;
          const a = rgba[at + 3] ?? 0;
          if (colors !== undefined && a > 0) weapon += (colors.mask[at] ?? 0) > 127 ? 1 : -1;
          alpha += a;
          r += (rgba[at] ?? 0) * a;
          g += (rgba[at + 1] ?? 0) * a;
          b += (rgba[at + 2] ?? 0) * a;
        }
      }
      if (alpha / (scale * scale) < 128) {
        row += '.';
        continue;
      }
      const allowed = colors === undefined ? undefined : weapon > 0 ? colors.weapon : colors.body;
      row += IMAGE_PIXELS[nearestColor(palette, r / alpha, g / alpha, b / alpha, allowed)] ?? IMAGE_PIXELS[0];
    }
    rows.push(row);
  }
  return rows;
}

/**
 * Paints a decal into the rows of a frame: each pixel whose block of scale x scale pixels in the `mask` image (white
 * the decal, black the rest) is half or more white takes the palette index `color`.
 */
export function paintDecal(rows: string[], mask: ArrayLike<number>, width: number, scale: number, color: number): void {
  const stride = width * scale;
  const digit = IMAGE_PIXELS[color];
  if (digit === undefined) throw new Error(`the decal color ${color} is not a palette index`);
  rows.forEach((row, y) => {
    let painted = '';
    for (let x = 0; x < row.length; x++) {
      let covered = 0;
      for (let sy = 0; sy < scale; sy++) {
        for (let sx = 0; sx < scale; sx++) {
          const at = ((y * scale + sy) * stride + x * scale + sx) * 4;
          if ((mask[at + 3] ?? 0) > 127 && (mask[at] ?? 0) > 127) covered++;
        }
      }
      painted += covered * 2 >= scale * scale ? digit : row[x];
    }
    rows[y] = painted;
  });
}
