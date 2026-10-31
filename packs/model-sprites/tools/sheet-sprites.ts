// Turns the color sheet that render-frames.ts rendered for a still model, such as a tree or a building, into palette
// facings in a types file, and writes a look sheet of the result. The color sheet has one cell per view facing, one
// under the other. Each block of scale x scale pixels becomes one game pixel; a block with an average alpha of 128 or
// more takes the palette color nearest to its average color in CIELAB, with the chroma raised by `--chroma`, and the
// rest is transparent. The facings are cut to the box of their visible pixels together, so they share one size and
// one anchor.
//
//   tsx sheet-sprites.ts --sheet <name>-color.png --palette <palette file> --out <types>.json --id <type>
//            --scale <sheet pixels per game pixel> --anchor-x <game pixel> --anchor-y <game pixel> [--chroma 1.6] [--facings 8]
//            [--extra '<JSON object>'] [--look <look.png>]
//
// Render the model at N times the sprite size. render-frames.ts draws each render pixel as 2 x 2 color sheet pixels
// (1 x 1 for a sheet wider than 16384 pixels), so `--scale` is 2N. The anchor is the game pixel of the foot of the
// model in the uncut frame: the render anchor divided by N. `--extra` adds its keys to the type, such as a footprint.
//
// The types file: { "types": { <id>: { ...extra, "anchorX", "anchorY", "facings": [[<row>, ...], ...] } } }. The id
// replaces a type of the same id; the other types stay. One line holds one pixel row.

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { cutFacings, decodePng, encodePng, lookSheet, paletteMatch, sheetFacings } from './palette-image';
import { parsePaletteText } from './pixels';

const LOOK_SCALE = 4;

interface SheetType {
  readonly [key: string]: unknown;
  readonly anchorX: number;
  readonly anchorY: number;
  readonly facings: readonly (readonly string[])[];
}

const { values } = parseArgs({
  options: {
    sheet: { type: 'string' },
    palette: { type: 'string' },
    out: { type: 'string' },
    id: { type: 'string' },
    scale: { type: 'string' },
    'anchor-x': { type: 'string' },
    'anchor-y': { type: 'string' },
    chroma: { type: 'string', default: '1.6' },
    facings: { type: 'string', default: '8' },
    extra: { type: 'string', default: '{}' },
    look: { type: 'string' },
  },
});
if (
  values.sheet === undefined ||
  values.palette === undefined ||
  values.out === undefined ||
  values.id === undefined ||
  values.scale === undefined ||
  values['anchor-x'] === undefined ||
  values['anchor-y'] === undefined
) {
  console.error('usage: tsx sheet-sprites.ts --sheet <name>-color.png --palette <palette file> --out <types>.json --id <type> --scale <n> --anchor-x <x> --anchor-y <y>');
  process.exit(1);
}

const paletteHex = parsePaletteText(readFileSync(resolve(values.palette), 'utf8'));
const sheet = decodePng(readFileSync(resolve(values.sheet)));
const uncut = sheetFacings(sheet, Number(values.scale), paletteMatch(paletteHex, Number(values.chroma)), Number(values.facings));
const { left, top, facings } = cutFacings(uncut);
const extra = JSON.parse(values.extra) as Record<string, unknown>;
const type: SheetType = { ...extra, anchorX: Number(values['anchor-x']) - left, anchorY: Number(values['anchor-y']) - top, facings };

/** The JSON text of the types: one line for each pixel row. */
function format(types: Readonly<Record<string, SheetType>>): string {
  const entries = Object.entries(types).map(([id, { facings: images, ...fields }]) => {
    const lines = Object.entries(fields).map(([key, value]) => `      ${JSON.stringify(key)}: ${JSON.stringify(value)},`);
    const rows = images.map((rows) => `        [\n${rows.map((row) => `          ${JSON.stringify(row)}`).join(',\n')}\n        ]`);
    return `    ${JSON.stringify(id)}: {\n${lines.join('\n')}\n      "facings": [\n${rows.join(',\n')}\n      ]\n    }`;
  });
  return `{\n  "types": {\n${entries.join(',\n')}\n  }\n}\n`;
}

const out = resolve(values.out);
const content = existsSync(out) ? (JSON.parse(readFileSync(out, 'utf8')) as { types: Record<string, SheetType> }) : { types: {} };
writeFileSync(out, format({ ...content.types, [values.id]: type }));
const frameWidth = facings[0]?.[0]?.length ?? 0;
const frameHeight = facings[0]?.length ?? 0;
console.log(`${values.out}: the type ${values.id}, ${frameWidth} x ${frameHeight} pixels, anchor (${type.anchorX}, ${type.anchorY})`);

if (values.look !== undefined) {
  writeFileSync(resolve(values.look), encodePng(lookSheet(facings, paletteHex, LOOK_SCALE)));
  console.log(`written: ${values.look}`);
}
