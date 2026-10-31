// Turns one animation of the frames that render-frames.ts wrote into a 1x PNG strip for a page that shows one
// direction only, such as a menu: the frames of one view facing side by side, each cut to the box that holds
// the visible pixels of every frame, and a JSON file with the frame size, the frame count and the frame time.
//
//   tsx strip-sheet.ts --frames <name>.json --palette <palette file> --clip <animation> --out <dir>/<strip name> [--facing 0]
//
// It writes <strip name>.png and <strip name>.json.

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { IMAGE_PIXELS } from './pixels';
import { encodePng } from './palette-image';
import { parsePaletteText } from './pixels';
import { parsePalette, type RenderedAnimation } from './sprite-render';

const { values } = parseArgs({
  options: { frames: { type: 'string' }, palette: { type: 'string' }, clip: { type: 'string' }, out: { type: 'string' }, facing: { type: 'string', default: '0' } },
});
if (values.frames === undefined || values.palette === undefined || values.clip === undefined || values.out === undefined) {
  console.error('usage: tsx strip-sheet.ts --frames <name>.json --palette <palette file> --clip <animation> --out <dir>/<strip name> [--facing 0]');
  process.exit(1);
}

const rendered = JSON.parse(readFileSync(resolve(values.frames), 'utf8')) as { animations: RenderedAnimation[] };
const animation = rendered.animations.find(({ clip }) => clip === values.clip);
const frames = animation?.facings[Number(values.facing)];
if (animation === undefined || frames === undefined || frames.length === 0) throw new Error(`${values.frames} has no animation ${values.clip}`);

let left = Infinity;
let right = -1;
let top = Infinity;
let bottom = -1;
for (const rows of frames) {
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === '.') continue;
      left = Math.min(left, x);
      right = Math.max(right, x);
      top = Math.min(top, y);
      bottom = Math.max(bottom, y);
    }
  });
}
if (right < 0) throw new Error(`the animation ${values.clip} has no visible pixel`);

const palette = parsePalette(parsePaletteText(readFileSync(resolve(values.palette), 'utf8')));
const frameWidth = right - left + 1;
const frameHeight = bottom - top + 1;
const width = frameWidth * frames.length;
const data = new Uint8Array(width * frameHeight * 4);
frames.forEach((rows, frame) => {
  for (let y = 0; y < frameHeight; y++) {
    const row = rows[top + y] ?? '';
    for (let x = 0; x < frameWidth; x++) {
      const digit = row[left + x] ?? '.';
      if (digit === '.') continue;
      const color = palette[IMAGE_PIXELS.indexOf(digit)];
      if (color === undefined) throw new Error(`the pixel ${digit} is not a palette color`);
      const at = (y * width + frame * frameWidth + x) * 4;
      data.set([...color, 255], at);
    }
  }
});

const out = resolve(values.out);
writeFileSync(`${out}.png`, encodePng({ width, height: frameHeight, data }));
writeFileSync(`${out}.json`, `${JSON.stringify({ frameWidth, frameHeight, frames: frames.length, frameMs: animation.frameMs })}\n`);
console.log(`written: ${out}.png (${frames.length} frames of ${frameWidth} x ${frameHeight}), ${out}.json`);
