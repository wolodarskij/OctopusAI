// The image code of the still-sheet converter and the strip sheet: PNG files, the palette match in CIELAB, the box
// filter of a color sheet to game pixels, the cut of the 8 facings to one box, and the look sheet of a result.

import { crc32, deflateSync, inflateSync } from 'node:zlib';
import { IMAGE_PIXELS } from './pixels';
import { FACINGS, parsePalette } from './sprite-render';

export interface Rgba {
  readonly width: number;
  readonly height: number;
  readonly data: Uint8Array;
}

/** Decodes an 8-bit RGBA or RGB PNG with no interlace: the kind a canvas writes. */
export function decodePng(file: Buffer): Rgba {
  let at = 8;
  let width = 0;
  let height = 0;
  let channels = 0;
  const idat: Buffer[] = [];
  while (at < file.length) {
    const length = file.readUInt32BE(at);
    const type = file.toString('ascii', at + 4, at + 8);
    const body = file.subarray(at + 8, at + 8 + length);
    if (type === 'IHDR') {
      width = body.readUInt32BE(0);
      height = body.readUInt32BE(4);
      const [depth, color, , , interlace] = [body[8], body[9], body[10], body[11], body[12]];
      if (depth !== 8 || interlace !== 0 || (color !== 6 && color !== 2)) throw new Error('only 8-bit RGBA or RGB PNG files with no interlace');
      channels = color === 6 ? 4 : 3;
    } else if (type === 'IDAT') {
      idat.push(body);
    }
    at += 12 + length;
  }
  const raw = inflateSync(Buffer.concat(idat));
  const stride = width * channels;
  const pixels = new Uint8Array(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    for (let x = 0; x < stride; x++) {
      const value = raw[y * (stride + 1) + 1 + x] ?? 0;
      const left = x >= channels ? (pixels[y * stride + x - channels] ?? 0) : 0;
      const up = y > 0 ? (pixels[(y - 1) * stride + x] ?? 0) : 0;
      const upLeft = x >= channels && y > 0 ? (pixels[(y - 1) * stride + x - channels] ?? 0) : 0;
      let predictor = 0;
      if (filter === 1) predictor = left;
      else if (filter === 2) predictor = up;
      else if (filter === 3) predictor = Math.floor((left + up) / 2);
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        predictor = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
      }
      pixels[y * stride + x] = (value + predictor) & 0xff;
    }
  }
  if (channels === 4) return { width, height, data: pixels };
  const data = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    data.set(pixels.subarray(i * 3, i * 3 + 3), i * 4);
    data[i * 4 + 3] = 255;
  }
  return { width, height, data };
}

export function encodePng(image: Rgba): Buffer {
  const chunk = (type: string, body: Buffer): Buffer => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(body.length, 0);
    head.write(type, 4, 'ascii');
    const tail = Buffer.alloc(4);
    tail.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), body])), 0);
    return Buffer.concat([head, body, tail]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(image.width, 0);
  header.writeUInt32BE(image.height, 4);
  header[8] = 8;
  header[9] = 6;
  const stride = image.width * 4;
  const raw = Buffer.alloc((stride + 1) * image.height);
  for (let y = 0; y < image.height; y++) raw.set(image.data.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1);
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/** sRGB 0..255 to CIELAB (D65). */
function lab(r: number, g: number, b: number): [number, number, number] {
  const linear = (c: number) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const [lr, lg, lb] = [linear(r), linear(g), linear(b)];
  const x = (lr * 0.4124 + lg * 0.3576 + lb * 0.1805) / 0.95047;
  const y = lr * 0.2126 + lg * 0.7152 + lb * 0.0722;
  const z = (lr * 0.0193 + lg * 0.1192 + lb * 0.9505) / 1.08883;
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (t * 24389) / 27 / 116 + 16 / 116);
  return [116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))];
}

/**
 * The palette match: the index of the palette color nearest to a color in CIELAB, with the chroma of the color
 * raised by `chroma`, so that a muted color keeps its hue among the strong colors of the palette.
 */
export function paletteMatch(paletteHex: readonly string[], chroma: number): (r: number, g: number, b: number) => number {
  const paletteLab = parsePalette(paletteHex).map(([r, g, b]) => lab(r, g, b));
  return (r, g, b) => {
    const [l, a, bb] = lab(r, g, b);
    let best = 0;
    let bestDistance = Infinity;
    paletteLab.forEach(([pl, pa, pb], index) => {
      const distance = (pl - l) ** 2 + (pa - a * chroma) ** 2 + (pb - bb * chroma) ** 2;
      if (distance < bestDistance) {
        best = index;
        bestDistance = distance;
      }
    });
    return best;
  };
}

/**
 * The game pixel rows of each view facing of a color sheet with one cell per facing, one under the other, uncut.
 * The sheet holds `facingCount` cells.
 * Each block of scale x scale pixels becomes one game pixel; a block with an average alpha of 128 or more takes the
 * color of `match` for its average color, and the rest is transparent.
 */
export function sheetFacings(sheet: Rgba, scale: number, match: (r: number, g: number, b: number) => number, facingCount = FACINGS): string[][] {
  const cellHeight = sheet.height / facingCount;
  const width = Math.floor(sheet.width / scale);
  const height = Math.floor(cellHeight / scale);
  return Array.from({ length: facingCount }, (_, facing) => {
    const rows: string[] = [];
    for (let y = 0; y < height; y++) {
      let row = '';
      for (let x = 0; x < width; x++) {
        let alpha = 0;
        let r = 0;
        let g = 0;
        let b = 0;
        for (let sy = 0; sy < scale; sy++) {
          for (let sx = 0; sx < scale; sx++) {
            const at = ((facing * cellHeight + y * scale + sy) * sheet.width + x * scale + sx) * 4;
            const a = sheet.data[at + 3] ?? 0;
            alpha += a;
            r += (sheet.data[at] ?? 0) * a;
            g += (sheet.data[at + 1] ?? 0) * a;
            b += (sheet.data[at + 2] ?? 0) * a;
          }
        }
        row += alpha / (scale * scale) < 128 ? '.' : (IMAGE_PIXELS[match(r / alpha, g / alpha, b / alpha)] ?? IMAGE_PIXELS[0]);
      }
      rows.push(row);
    }
    return rows;
  });
}

/** The facings cut to the box of their visible pixels together, so they share one size; `left` and `top` of the box. */
export function cutFacings(uncut: readonly (readonly string[])[]): { left: number; top: number; facings: string[][] } {
  let left = Infinity;
  let right = -1;
  let top = Infinity;
  let bottom = -1;
  for (const rows of uncut) {
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
  if (right < 0) throw new Error('the sheet has no visible pixel');
  return { left, top, facings: uncut.map((rows) => rows.slice(top, bottom + 1).map((row) => row.slice(left, right + 1))) };
}

/** A look sheet of the facings side by side, `scale` screen pixels per game pixel. */
export function lookSheet(facings: readonly (readonly string[])[], paletteHex: readonly string[], scale: number): Rgba {
  const frameWidth = facings[0]?.[0]?.length ?? 0;
  const frameHeight = facings[0]?.length ?? 0;
  const look: Rgba = { width: frameWidth * facings.length * scale, height: frameHeight * scale, data: new Uint8Array(frameWidth * facings.length * scale * frameHeight * scale * 4) };
  const colors = parsePalette(paletteHex);
  facings.forEach((rows, facing) => {
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const pixel = row[x];
        if (pixel === undefined || pixel === '.') continue;
        const [r, g, b] = colors[IMAGE_PIXELS.indexOf(pixel)] ?? [255, 0, 255];
        for (let sy = 0; sy < scale; sy++) {
          for (let sx = 0; sx < scale; sx++) {
            const at = ((y * scale + sy) * look.width + (facing * frameWidth + x) * scale + sx) * 4;
            look.data.set([r, g, b, 255], at);
          }
        }
      }
    });
  });
  return look;
}
