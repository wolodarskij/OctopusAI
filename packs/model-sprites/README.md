# The `model-sprites` pack

A 3D model (GLB) becomes palette pixel sprites in 8 facings, seen from a fixed orthographic 2:1 camera, with
sampled animation clips, stored as rows of palette characters in JSON. A still model becomes palette facings with
a CIELAB palette match. A video becomes a small black-and-white dithered MP4. The pack contract is
`packs/PACKS.md`.

## What the pack holds

| Path | What it is |
|---|---|
| `ruleset.json` | the title and the payload |
| `skills/model-sprites/SKILL.md` | the skill: setup, the animated and the still route, the video converter. Install copies it to `<project>/.agents/skills/model-sprites/` |
| `tools/package.json`, `tools/package-lock.json` | the dependencies of the tools: `three`, `vite`, `tsx` |
| `tools/render-frames.ts` | the render: serves `render.html` with Vite, drives a headless Chrome over the DevTools protocol, writes the frames and two look sheets |
| `tools/render.html`, `tools/render-page.ts` | the browser half: loads the GLB with three.js, poses each clip, renders each facing, reduces each render to palette pixels |
| `tools/sprite-render.ts` | the settings and result types, the camera, the frame times, the RGB palette match, the pixel reduction |
| `tools/pixels.ts` | the image row characters and the palette file reader |
| `tools/pose.ts`, `weapon.ts`, `parts.ts`, `rotors.ts`, `wings.ts`, `decals.ts` | the layers of the render page: crouch, ground sit and controller poses on a Tripo biped rig; a weapon in a hand bone; a hull and a turret; turning rotors; beating wings; lettering in one palette color |
| `tools/palette-image.ts` | PNG read and write, the CIELAB palette match, the box filter of a color sheet, the facing cut, the look sheet |
| `tools/sheet-sprites.ts` | a color sheet of a still model to palette facings in a types file |
| `tools/crop-frames.ts` | rendered frames to a frame set file, each frame cut to its visible box |
| `tools/strip-sheet.ts` | one animation of one facing to a 1x PNG strip and its JSON |
| `tools/static-clip.ts` | adds the one-key clip `static` to a GLB with no animation |
| `tools/video-dither.ts` | a video, or each video of a folder, to a dithered black-and-white MP4 |

## Setup

`npm install --prefix <Octopus AI>/packs/model-sprites/tools`. Run each tool with the installed `tsx`, from the
project root, so that relative paths resolve in the project:

```
<Octopus AI>/packs/model-sprites/tools/node_modules/.bin/tsx <Octopus AI>/packs/model-sprites/tools/<tool>.ts ...
```

**Dependencies:** Node.js 20 or later; Chrome, Chromium or Edge (`--chrome`, else `CHROME_PATH`, else the usual
install paths); `ffmpeg` and `ffprobe` on PATH for `video-dither.ts`.

## The command lines

```
render-frames.ts --model <glb> --palette <file> --out <dir> --name <name>
                 [--clips <clip>:<frames>:loop|once,...] [--clips-file <json>] [--clip-model <glb>]...
                 [--width 48] [--height 40] [--anchor-x 24] [--anchor-y 34] [--body-height 26] [--supersample 8]
                 [--yaw-offset -90] [--light 1] [--metalness <f>] [--facings <n>]
                 [--weapon <json>] [--body-colors <i,...>] [--parts <json> --part all|hull|turret]
                 [--rotors <json>] [--wings <json>] [--decals <json>]
                 [--footprint <w>x<d> --building-height <tiles> --tile-pixels 32x16]
                 [--chrome <path>] [--scratch <dir>] [--port 5190]
sheet-sprites.ts --sheet <color.png> --palette <file> --out <types.json> --id <type> --scale <n>
                 --anchor-x <x> --anchor-y <y> [--chroma 1.6] [--facings 8] [--extra <json>] [--look <png>]
crop-frames.ts   --frames <json> [--frames <json>]... --set <name> --out <frame sets.json>
strip-sheet.ts   --frames <json> --palette <file> --clip <clip> --out <dir>/<strip> [--facing 0]
static-clip.ts   <in.glb> <out.glb>
video-dither.ts  --source <video or folder> --out <dir> --width <px> --height <px> [--dither 4] [--upscale 4]
```

The settings files of `--clips-file`, `--weapon`, `--parts`, `--rotors`, `--wings` and `--decals` hold the
interfaces `ClipRequest[]`, `WeaponSettings`, `PartSettings`, `RotorSettings`, `WingSettings` and
`DecalSettings[]` of `tools/sprite-render.ts`. A weapon file holds `model`, the path of the weapon GLB relative to
the weapon file, in place of `modelUrl`.

## The files the tools write

| File | Content |
|---|---|
| `<name>.json` of `render-frames.ts` | `{ "frame": { width, height, anchorX, anchorY }, "animations": [{ clip, frameMs, loop, facings: [[rows]] }] }`; `facings[viewFacing][frame]` |
| `<name>-color.png`, `<name>-pixels.png` | the full color renders and the palette sprites, one row per facing, one column per frame |
| the frame set file of `crop-frames.ts` | `{ "sets": { <set>: { frame, animations: { <clip>: { frameMs, loop, facings: [[{ x, y, rows }]] } } } } }` |
| the types file of `sheet-sprites.ts` | `{ "types": { <id>: { ...extra, anchorX, anchorY, facings: [rows] } } }` |
| `<strip>.png`, `<strip>.json` of `strip-sheet.ts` | the strip, and `{ frameWidth, frameHeight, frames, frameMs }` |

A row is a string: `.` is transparent, the character at index i of `pixels.ts` `IMAGE_PIXELS` is palette color i.
The camera: 8 facings in 45-degree steps (`FACINGS`), pitch 30 degrees (`PITCH_DEGREES`); facing 0 is screen
down-right, 1 screen down, 7 screen right (`facingYaw` in `sprite-render.ts`). A game that draws the sprites uses
the same facing order.
