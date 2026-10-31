// Renders the sprite frames of an animated GLB, such as a Tripo model: it serves render.html with Vite, opens it in a
// headless Chrome over the DevTools protocol, and writes the palette pixel frames and two look sheets.
//
//   tsx render-frames.ts --model <animated.glb> --palette <palette file> --out <dir> --name <name>
//   the palette file: see parsePaletteText in pixels.ts
//   options: --clips walk:8:loop,fall:6:once or --clips-file <clips.json>, a list of ClipRequest   --width 48 --height 40 --anchor-x 24 --anchor-y 34
//            --body-height 26 --supersample 8 --yaw-offset -90 --light 1 --metalness 0.3 --chrome <chrome or edge> --scratch <dir> --port 5190
//            --clip-model <more.glb> (repeatable): the clips of another GLB of the same rig
//            --weapon <weapon.json>: a WeaponSettings with `model`, the weapon GLB path from the JSON file, for `modelUrl`
//            --body-colors 0,1,2: the palette indices of the body pixels when a weapon is held
//            --footprint 3x2 --building-height 2 --tile-pixels 32x16: a building of 3 x 2 tiles (width x depth) and
//            2 tiles high, in a view whose tile diamond is 32 pixels wide and whose tile of height is 16 pixels
//            --parts <parts.json> --part hull|turret|all: a still vehicle model in parts (PartSettings with no `part`),
//            and the part to render
//            --rotors <rotors.json>: a still drone model whose rotors turn (RotorSettings)
//            --wings <wings.json>: a still bird model whose wings beat (WingSettings)
//            --facings 1: render only the first view facings
//            --decals <decals.json>: a list of DecalSettings, the lettering of the model in one palette color each
//
// It writes <out>/<name>.json (the frames in the image row format of pixels.ts), <out>/<name>-color.png
// (the full color renders) and <out>/<name>-pixels.png (the palette sprites), one row per view facing.

import { spawn } from 'node:child_process';
import { createReadStream, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { createServer, type Plugin } from 'vite';
import { parsePaletteText } from './pixels';
import type { ClipRequest, DecalSettings, FootprintSettings, PartSettings, RenderResult, RenderSettings, RotorSettings, WeaponSettings, WingSettings } from './sprite-render';

/** The directory of this tool: the Vite server serves render.html and its modules from it. */
const TOOLS = import.meta.dirname;
const MODEL_PATH = '/__model/model.glb';
const WEAPON_PATH = '/__model/weapon.glb';

const { values } = parseArgs({
  options: {
    model: { type: 'string' },
    palette: { type: 'string' },
    out: { type: 'string' },
    name: { type: 'string' },
    clips: { type: 'string', default: 'walk:8:loop,fall:6:once' },
    'clips-file': { type: 'string' },
    width: { type: 'string', default: '48' },
    height: { type: 'string', default: '40' },
    'anchor-x': { type: 'string', default: '24' },
    'anchor-y': { type: 'string', default: '34' },
    'body-height': { type: 'string', default: '26' },
    supersample: { type: 'string', default: '8' },
    'yaw-offset': { type: 'string', default: '-90' },
    light: { type: 'string', default: '1' },
    metalness: { type: 'string' },
    'clip-model': { type: 'string', multiple: true, default: [] },
    weapon: { type: 'string' },
    'body-colors': { type: 'string' },
    footprint: { type: 'string' },
    'building-height': { type: 'string' },
    'tile-pixels': { type: 'string', default: '32x16' },
    parts: { type: 'string' },
    part: { type: 'string', default: 'all' },
    rotors: { type: 'string' },
    wings: { type: 'string' },
    facings: { type: 'string' },
    decals: { type: 'string' },
    chrome: { type: 'string' },
    scratch: { type: 'string', default: join(tmpdir(), 'model-sprites-chrome') },
    port: { type: 'string', default: '5190' },
  },
});

if (values.model === undefined || values.palette === undefined || values.out === undefined || values.name === undefined) {
  console.error('usage: tsx render-frames.ts --model <animated.glb> --palette <palette file> --out <dir> --name <name>');
  process.exit(1);
}
const modelFile = resolve(values.model);
const outDir = resolve(values.out);
const name = values.name;

function parseClips(text: string): ClipRequest[] {
  return text.split(',').map((entry) => {
    const [clip, frames, mode] = entry.split(':');
    if (clip === undefined || frames === undefined || !(mode === 'loop' || mode === 'once')) {
      throw new Error(`the clip ${entry} is not <clip>:<frames>:loop|once`);
    }
    return { clip, frames: Number(frames), loop: mode === 'loop' };
  });
}

/** The footprint settings of a building from the options `--footprint`, `--building-height` and `--tile-pixels`. */
function footprintOf(footprint: string | undefined, height: string | undefined, tilePixels: string): FootprintSettings | undefined {
  if (footprint === undefined) return undefined;
  const [width, depth] = footprint.split('x').map(Number);
  const [tileWidth, tileHeight] = tilePixels.split('x').map(Number);
  if (width === undefined || depth === undefined || tileWidth === undefined || tileHeight === undefined || height === undefined) {
    throw new Error('a building needs --footprint <width>x<depth>, --building-height <tiles> and --tile-pixels <width>x<height>');
  }
  return { width, depth, height: Number(height), tileWidth, tileHeight };
}

/** Serves each model file at its URL path. */
function serveModels(files: ReadonlyMap<string, string>): Plugin {
  return {
    name: 'model-sprites-models',
    configureServer(server) {
      for (const [path, file] of files) {
        server.middlewares.use(path, (_request, response) => {
          response.setHeader('Content-Type', 'model/gltf-binary');
          response.setHeader('Content-Length', statSync(file).size);
          createReadStream(file).pipe(response);
        });
      }
    },
  };
}

/** Reads a weapon file: the WeaponSettings with `model`, the path of the weapon GLB from the file, for `modelUrl`. */
function readWeapon(file: string): { settings: WeaponSettings; model: string } {
  const data = JSON.parse(readFileSync(file, 'utf8')) as Omit<WeaponSettings, 'modelUrl'> & { model: string };
  const { model, ...rest } = data;
  return { settings: { ...rest, modelUrl: WEAPON_PATH }, model: resolve(dirname(file), model) };
}

/** Reads a parts file: the PartSettings with no `part`, which `--part` gives. */
function readParts(file: string | undefined, part: string): PartSettings | undefined {
  if (file === undefined) return undefined;
  if (part !== 'all' && part !== 'hull' && part !== 'turret') throw new Error(`--part ${part} is not all, hull or turret`);
  return { ...(JSON.parse(readFileSync(file, 'utf8')) as Omit<PartSettings, 'part'>), part };
}

/** The browser: `--chrome`, then the CHROME_PATH variable, then the usual install paths of Chrome, Chromium and Edge. */
function findChrome(option: string | undefined): string {
  const candidates = [
    option,
    process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    process.env.LOCALAPPDATA === undefined ? undefined : join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'),
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ];
  const found = candidates.find((path) => path !== undefined && existsSync(path));
  if (found === undefined) throw new Error('no Chrome, Chromium or Edge found: pass --chrome <path> or set CHROME_PATH');
  return found;
}

/** Starts a headless Chrome and returns the WebSocket URL of its DevTools endpoint. */
function startChrome(chrome: string, profile: string): Promise<{ url: string; stop: () => void }> {
  mkdirSync(profile, { recursive: true });
  const child = spawn(chrome, [
    '--headless=new',
    '--remote-debugging-port=0',
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    'about:blank',
  ]);
  return new Promise((resolveUrl, reject) => {
    let log = '';
    child.stderr.on('data', (chunk: Buffer) => {
      log += chunk.toString();
      const match = /DevTools listening on (ws:\/\/\S+)/.exec(log);
      if (match?.[1] !== undefined) resolveUrl({ url: match[1], stop: () => child.kill() });
    });
    child.on('error', reject);
    child.on('exit', (code) => reject(new Error(`chrome exited with ${code}: ${log}`)));
  });
}

/** A DevTools protocol connection with one attached page session. */
class DevTools {
  private nextId = 1;
  private readonly pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();

  private constructor(private readonly socket: WebSocket) {
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(String(event.data)) as { id?: number; result?: unknown; error?: { message: string } };
      if (message.id === undefined) return;
      const waiter = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error !== undefined) waiter?.reject(new Error(message.error.message));
      else waiter?.resolve(message.result);
    });
  }

  static connect(url: string): Promise<DevTools> {
    const socket = new WebSocket(url);
    return new Promise((resolveSocket, reject) => {
      socket.addEventListener('open', () => resolveSocket(new DevTools(socket)));
      socket.addEventListener('error', () => reject(new Error(`no DevTools connection at ${url}`)));
    });
  }

  send<T>(method: string, params: object = {}, sessionId?: string): Promise<T> {
    const id = this.nextId++;
    this.socket.send(JSON.stringify({ id, method, params, sessionId }));
    return new Promise((resolveResult, reject) => {
      this.pending.set(id, { resolve: (value) => resolveResult(value as T), reject });
    });
  }

  close(): void {
    this.socket.close();
  }
}

interface Evaluation {
  readonly result: { readonly value?: unknown };
  readonly exceptionDetails?: { readonly text: string; readonly exception?: { readonly description?: string } };
}

async function evaluate(devTools: DevTools, sessionId: string, expression: string): Promise<unknown> {
  const answer = await devTools.send<Evaluation>('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }, sessionId);
  if (answer.exceptionDetails !== undefined) {
    throw new Error(answer.exceptionDetails.exception?.description ?? answer.exceptionDetails.text);
  }
  return answer.result.value;
}

function writeDataUrl(path: string, dataUrl: string): void {
  writeFileSync(path, Buffer.from(dataUrl.slice(dataUrl.indexOf(',') + 1), 'base64'));
}

const models = new Map<string, string>([[MODEL_PATH, modelFile]]);
const clipModelUrls = values['clip-model'].map((file, index) => {
  const path = `/__model/clips-${index}.glb`;
  models.set(path, resolve(file));
  return path;
});
const weapon = values.weapon === undefined ? undefined : readWeapon(resolve(values.weapon));
if (weapon !== undefined) models.set(WEAPON_PATH, weapon.model);

const palette = parsePaletteText(readFileSync(resolve(values.palette), 'utf8'));
const settings: RenderSettings = {
  modelUrl: MODEL_PATH,
  width: Number(values.width),
  height: Number(values.height),
  anchorX: Number(values['anchor-x']),
  anchorY: Number(values['anchor-y']),
  bodyHeight: Number(values['body-height']),
  supersample: Number(values.supersample),
  yawOffset: Number(values['yaw-offset']),
  light: Number(values.light),
  metalness: values.metalness === undefined ? undefined : Number(values.metalness),
  clips: values['clips-file'] === undefined ? parseClips(values.clips) : (JSON.parse(readFileSync(values['clips-file'], 'utf8')) as ClipRequest[]),
  palette,
  clipModelUrls,
  weapon: weapon?.settings,
  bodyColors: values['body-colors']?.split(',').map(Number),
  footprint: footprintOf(values.footprint, values['building-height'], values['tile-pixels']),
  parts: readParts(values.parts, values.part),
  rotors: values.rotors === undefined ? undefined : (JSON.parse(readFileSync(values.rotors, 'utf8')) as RotorSettings),
  wings: values.wings === undefined ? undefined : (JSON.parse(readFileSync(values.wings, 'utf8')) as WingSettings),
  facings: values.facings === undefined ? undefined : Number(values.facings),
  decals: values.decals === undefined ? undefined : (JSON.parse(readFileSync(values.decals, 'utf8')) as DecalSettings[]),
};

const server = await createServer({
  root: TOOLS,
  configFile: false,
  logLevel: 'error',
  plugins: [serveModels(models)],
  server: { port: Number(values.port), strictPort: false, watch: null },
});
await server.listen();
const base = server.resolvedUrls?.local[0];
const chrome = await startChrome(findChrome(values.chrome), resolve(values.scratch));
try {
  if (base === undefined) throw new Error('the Vite server has no local URL');
  const devTools = await DevTools.connect(chrome.url);
  const { targetId } = await devTools.send<{ targetId: string }>('Target.createTarget', { url: `${base}render.html` });
  const { sessionId } = await devTools.send<{ sessionId: string }>('Target.attachToTarget', { targetId, flatten: true });
  const deadline = Date.now() + 60_000;
  while ((await evaluate(devTools, sessionId, 'document.title')) !== 'ready') {
    if (Date.now() > deadline) throw new Error('the render page did not load within 60 seconds');
    await new Promise((wait) => setTimeout(wait, 250));
  }
  const result = (await evaluate(devTools, sessionId, `window.renderSprites(${JSON.stringify(settings)})`)) as RenderResult;
  devTools.close();

  mkdirSync(outDir, { recursive: true });
  const frame = { width: settings.width, height: settings.height, anchorX: settings.anchorX, anchorY: settings.anchorY };
  writeFileSync(join(outDir, `${name}.json`), `${JSON.stringify({ frame, animations: result.animations })}\n`);
  writeDataUrl(join(outDir, `${name}-color.png`), result.colorSheet);
  writeDataUrl(join(outDir, `${name}-pixels.png`), result.pixelSheet);
  for (const animation of result.animations) {
    console.log(`${animation.clip}: ${animation.facings.length} facings x ${animation.facings[0]?.length ?? 0} frames, ${animation.frameMs} ms per frame`);
  }
  console.log(`written: ${join(outDir, name)}.json, -color.png, -pixels.png`);
} finally {
  chrome.stop();
  await server.close();
}
