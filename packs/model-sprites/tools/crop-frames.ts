// Puts the frames that render-frames.ts wrote into a frame set file as one set, each frame cut to the box of its
// visible pixels. The set replaces a set of the same name; the other sets of the file stay. More `--frames` files add
// their animations to the set, in order; their frames must have the size and the anchor of the first file.
//
//   tsx crop-frames.ts --frames <name>.json [--frames <more>.json] --set <set name> --out <frame sets>.json
//
// The frame set file: { "sets": { <set>: { "frame": { width, height, anchorX, anchorY }, "animations": { <clip>:
// { frameMs, loop, facings: [[{ x, y, rows }]] } } } } }. `x` and `y` place the cut rows in the uncut frame, so the
// anchor of the uncut frame stays valid. One line holds one frame.

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import type { RenderedAnimation } from './sprite-render';

interface Frame {
  readonly width: number;
  readonly height: number;
  readonly anchorX: number;
  readonly anchorY: number;
}

interface FrameImage {
  readonly x: number;
  readonly y: number;
  readonly rows: readonly string[];
}

interface FrameAnimation {
  readonly frameMs: number;
  readonly loop: boolean;
  readonly facings: readonly (readonly FrameImage[])[];
}

interface FrameSet {
  readonly frame: Frame;
  readonly animations: Readonly<Record<string, FrameAnimation>>;
}

interface FrameSets {
  readonly sets: Readonly<Record<string, FrameSet>>;
}

const { values } = parseArgs({ options: { frames: { type: 'string', multiple: true }, set: { type: 'string' }, out: { type: 'string' } } });
if (values.frames === undefined || values.frames.length === 0 || values.set === undefined || values.out === undefined) {
  console.error('usage: tsx crop-frames.ts --frames <name>.json [--frames <more>.json] --set <set name> --out <frame sets>.json');
  process.exit(1);
}

/** Cuts the rows of a frame to the box of its visible pixels; an empty frame becomes one transparent pixel. */
function cropFrame(rows: readonly string[]): FrameImage {
  let left = Infinity;
  let right = -1;
  let top = Infinity;
  let bottom = -1;
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === '.') continue;
      left = Math.min(left, x);
      right = Math.max(right, x);
      top = Math.min(top, y);
      bottom = Math.max(bottom, y);
    }
  });
  if (right < 0) return { x: 0, y: 0, rows: ['.'] };
  return { x: left, y: top, rows: rows.slice(top, bottom + 1).map((row) => row.slice(left, right + 1)) };
}

/** The JSON text of the frame sets: one line for each frame. */
function format(content: FrameSets): string {
  const sets = Object.entries(content.sets).map(([name, set]) => {
    const animations = Object.entries(set.animations).map(([clip, animation]) => {
      const facings = animation.facings.map((frames) => `            [\n${frames.map((frame) => `              ${JSON.stringify(frame)}`).join(',\n')}\n            ]`);
      return `        ${JSON.stringify(clip)}: {\n          "frameMs": ${animation.frameMs},\n          "loop": ${animation.loop},\n          "facings": [\n${facings.join(',\n')}\n          ]\n        }`;
    });
    return `    ${JSON.stringify(name)}: {\n      "frame": ${JSON.stringify(set.frame)},\n      "animations": {\n${animations.join(',\n')}\n      }\n    }`;
  });
  return `{\n  "sets": {\n${sets.join(',\n')}\n  }\n}\n`;
}

interface Rendered {
  frame: Frame;
  animations: RenderedAnimation[];
}
const files = values.frames.map((file) => JSON.parse(readFileSync(resolve(file), 'utf8')) as Rendered);
const rendered = files[0] as Rendered;
const animations: Record<string, FrameAnimation> = {};
for (const [index, file] of files.entries()) {
  if (JSON.stringify(file.frame) !== JSON.stringify(rendered.frame)) throw new Error(`${values.frames[index]} has another frame size or anchor`);
  for (const animation of file.animations) {
    animations[animation.clip] = {
      frameMs: animation.frameMs,
      loop: animation.loop,
      facings: animation.facings.map((frames) => frames.map(cropFrame)),
    };
  }
}
const out = resolve(values.out);
const content: FrameSets = existsSync(out) ? (JSON.parse(readFileSync(out, 'utf8')) as FrameSets) : { sets: {} };
writeFileSync(out, format({ sets: { ...content.sets, [values.set]: { frame: rendered.frame, animations } } }));
console.log(`${values.out}: the set ${values.set} with ${Object.keys(animations).join(', ')}`);
