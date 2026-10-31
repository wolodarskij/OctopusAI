---
name: model-sprites
description: Turn a 3D model (a GLB, such as one from Tripo) into palette pixel sprites - 8 facings from a fixed 2:1 orthographic camera, sampled animation clips, rows of palette characters in JSON - and turn videos into small black-and-white dithered MP4 files. Use when asked to "render the model to sprites", "make sprite frames from the GLB", "make the walk animation sprites", "put the tree model into the palette", "make a sprite strip", "convert the intro video to dithered black and white", or when a new Tripo model must reach the game as pixel art.
---

# Model to palette sprites

The tools of this pack stay in Octopus AI and run from there. Every path on their command line is relative to
the current directory: run them from the project root, and keep the sources and the output in the project.

```bash
M="{{OCTOPUS}}/packs/model-sprites/tools"
TSX="$M/node_modules/.bin/tsx"
```

## Set up

1. Once per machine: `npm install --prefix "{{OCTOPUS}}/packs/model-sprites/tools"`. It installs `three`, `vite`
   and `tsx`. Node.js 20 or later.
2. Chrome, Chromium or Edge. The render finds the usual install paths; else pass `--chrome <path>` or set
   `CHROME_PATH`. It runs headless, with no window.
3. `ffmpeg` and `ffprobe` on PATH, for `video-dither.ts` only.
4. A palette file of at most 64 colors: a JSON object `{"colors": [...], "extended": [...]}`, a JSON list, a GIMP
   `.gpl` file, or one hex color per line. The index of a color is its place in the file.

## The output format

A frame is a list of rows, one string per pixel row, top first. `.` is transparent; the character at index i of
`0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ#@` is palette color i.

The camera: 8 facings in 45-degree steps, pitch 30 degrees (the ground at half height). Facing 0 is screen
down-right, facing 1 screen down, facing 7 screen right. The game's sprite lookup must use the same order.

## Animated model

1. Get the model. A Tripo biped: `tripo make <image> --then rig`, then `tripo anim retarget <rig task>
   --animation preset:idle preset:walk ... --animate-in-place --out-format glb` (the `tripo-cli` skill).
2. Render it. Run it in the background: it starts a Vite server and a headless browser.

   ```bash
   "$TSX" "$M/render-frames.ts" --model <anim.glb> --palette <palette> --out <dir> --name <name> \
     --clips walk:8:loop,fall:6:once --width 48 --height 40 --anchor-x 24 --anchor-y 34 --body-height 26
   ```

   - `--clips <clip>:<frames>:loop|once`, or `--clips-file <clips.json>`: a list of `ClipRequest` that can also
     rename a clip, take a part of it (`start`, `end` in seconds), lay a pose over it, or blend two clips.
   - `--clip-model <more.glb>` (repeatable): the clips of another GLB of the same rig.
   - `--body-height`: the standing height in game pixels. `--anchor-x`, `--anchor-y`: the feet in the frame.
   - `--yaw-offset -90` turns a Tripo rig, whose front is +X, to the camera's front. `--light`, `--metalness`
     correct a model that renders too dark.
   - `--weapon <weapon.json>` and `--body-colors <i,j,...>`: a weapon in a hand bone, with its own palette
     colors, so that a recolor of the body leaves it alone.
   - `--parts`, `--rotors`, `--wings`, `--decals`, `--footprint`: a vehicle in hull and turret, turning rotors,
     beating wings, lettering in one color, a building on a tile footprint.

   The settings files are the interfaces in `{{OCTOPUS}}/packs/model-sprites/tools/sprite-render.ts`. It writes
   `<name>.json` (the frames), `<name>-color.png` and `<name>-pixels.png`. Look at both PNG files before you go on.
3. Cut the frames into a frame set file:

   ```bash
   "$TSX" "$M/crop-frames.ts" --frames <name>.json --set <set> --out <frame sets>.json
   ```

   Each frame is cut to its visible box, with `x` and `y` of the box in the uncut frame.
4. A strip for a page that shows one direction, such as a menu:

   ```bash
   "$TSX" "$M/strip-sheet.ts" --frames <name>.json --palette <palette> --clip <clip> --out <dir>/<strip> [--facing 0]
   ```

## Still model (a tree, a building, a prop)

1. Give it a one-key clip: `"$TSX" "$M/static-clip.ts" <model.glb> <static.glb>`.
2. Render it at N times the sprite size, one frame, with no palette step that you keep:

   ```bash
   "$TSX" "$M/render-frames.ts" --model <static.glb> --palette <palette> --out <dir> --name <name> \
     --clips static:1:once --width 256 --height 288 --anchor-x 128 --anchor-y 272 --body-height 80 \
     --supersample 2 --yaw-offset 0
   ```

3. Convert the color sheet with the palette match in CIELAB, which keeps the hue of a muted color:

   ```bash
   "$TSX" "$M/sheet-sprites.ts" --sheet <name>-color.png --palette <palette> --out <types>.json --id <type> \
     --scale 8 --anchor-x 32 --anchor-y 68 [--chroma 1.6] [--extra '{"footprint":[2,2]}'] --look <name>-look.png
   ```

   `--scale` is 2N (the color sheet draws each render pixel as 2 x 2), and the anchor is the render anchor
   divided by N. Raise `--chroma` for a dull result, lower it for a garish one.

## Video

```bash
"$TSX" "$M/video-dither.ts" --source <video or folder> --out <folder> --width 320 --height 180 [--dither 4] [--upscale 4]
```

Each frame fits inside the size with black bars and becomes black and white by a Bayer matrix of 2, 4 or 8. The
output is an H.264 MP4 with no audio, each pixel scaled up to an upscale x upscale block.

## Record it

Write each command with its options into the `SOURCE.md` of the asset's source folder, next to the model, so a
later session makes the sprites again.
