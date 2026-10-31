// The dithered video converter: each frame drops to a low resolution and becomes black and white with ordered
// dithering. It runs ffmpeg and ffprobe from PATH: one ffmpeg decodes the source to raw grey frames, the other encodes
// the result.
//
//   tsx video-dither.ts --source <video or folder> --out <folder> --width 320 --height 180 [--dither 4] [--upscale 4]
//
// A folder converts each file with a video stream, in name order, to <out>/<name>.mp4. Exits 1 when a file fails.

import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { basename, extname, join, resolve } from 'node:path';
import { parseArgs } from 'node:util';

/** The settings of one conversion. */
export interface DitherConversion {
  /** The size of a converted frame, in pixels. */
  readonly width: number;
  readonly height: number;
  /** The side of the Bayer matrix: 2, 4 or 8. */
  readonly dither: number;
  /** The whole-number scale of the encoded frame; each converted pixel becomes an upscale x upscale block. */
  readonly upscale: number;
}

/** The Bayer matrix of side n (a power of 2), row by row, with the values 0 to n*n - 1. */
export function bayerMatrix(n: number): number[] {
  let matrix = [0];
  for (let side = 1; side < n; side *= 2) {
    const next: number[] = new Array<number>(side * side * 4);
    for (let y = 0; y < side; y++) {
      for (let x = 0; x < side; x++) {
        const value = 4 * (matrix[y * side + x] ?? 0);
        next[y * side * 2 + x] = value;
        next[y * side * 2 + x + side] = value + 2;
        next[(y + side) * side * 2 + x] = value + 3;
        next[(y + side) * side * 2 + x + side] = value + 1;
      }
    }
    matrix = next;
  }
  return matrix;
}

/** Replaces each grey pixel of a width x height gray8 frame, in place, with 0 or 255 by ordered dithering. */
export function ditherFrame(frame: Uint8Array, width: number, matrix: readonly number[]): void {
  const side = Math.round(Math.sqrt(matrix.length));
  const cells = matrix.length;
  for (let at = 0; at < frame.length; at++) {
    const x = at % width;
    const y = Math.floor(at / width);
    const threshold = (((matrix[(y % side) * side + (x % side)] ?? 0) + 0.5) * 256) / cells;
    frame[at] = (frame[at] ?? 0) >= threshold ? 255 : 0;
  }
}

interface Run {
  readonly code: number | null;
  readonly stdout: string;
  readonly stderr: string;
}

/** Runs a command to its end. A command that cannot start gives the code null and the error in stderr. */
function run(command: string, args: readonly string[]): Promise<Run> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => (stdout += chunk.toString()));
    child.stderr.on('data', (chunk: Buffer) => (stderr += chunk.toString()));
    child.on('error', (error) => resolve({ code: null, stdout, stderr: stderr + error.message }));
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

/** True when ffprobe finds a video stream in the file. */
export async function hasVideoStream(path: string): Promise<boolean> {
  const probe = await run('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', path]);
  return probe.code === 0 && probe.stdout.trim() === 'video';
}

/**
 * The encode of a converted video: H.264 High, yuv420p, MP4, which every browser plays. With an upscale of 4, each
 * converted pixel fills one 4x4 transform block of H.264, so the encode of a black and white frame stays small; an
 * encode at the converted size is larger. The deblocking filter, the psychovisual optimizations and the adaptive
 * quantization are off: they spend bits on detail that a black and white frame does not have. One keyframe serves a
 * whole video, for a player that plays each video from its start and never seeks.
 */
const H264_ENCODE = [
  '-c:v', 'libx264', '-threads', '2', '-preset', 'veryslow', '-crf', '20', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
  '-x264-params', 'deblock=-6,-6:psy-rd=0,0:aq-mode=0:keyint=999:min-keyint=999', '-movflags', '+faststart',
];

/**
 * Converts a video to a black and white video: the frame fits inside width x height with black bars, each pixel
 * becomes black or white by ordered dithering, and each pixel is scaled up to an upscale x upscale block. The output
 * is an H.264 yuv420p MP4 with no audio, at the frame rate of the source. Returns true when the output file exists
 * and is not empty. Never throws.
 */
export async function convertDitheredVideo(inputPath: string, outputPath: string, conversion: DitherConversion): Promise<boolean> {
  const { width, height, dither, upscale } = conversion;
  try {
    const probe = await run('ffprobe', ['-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', inputPath]);
    const rate = probe.stdout.trim().split(/\r?\n/)[0] ?? '';
    if (probe.code !== 0 || !/^\d+(\/\d+)?$/.test(rate)) {
      console.error(`${inputPath}: no video stream\n${probe.stderr}`);
      return false;
    }

    const filter = `scale=${width}:${height}:force_original_aspect_ratio=decrease:flags=area,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2:black`;
    const decoder = spawn('ffmpeg', ['-v', 'error', '-i', inputPath, '-an', '-vf', filter, '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1'], {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const encoder = spawn(
      'ffmpeg',
      ['-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'gray', '-s', `${width}x${height}`, '-r', rate, '-i', 'pipe:0',
        '-an', '-vf', `scale=${width * upscale}:${height * upscale}:flags=neighbor`,
        ...H264_ENCODE, outputPath],
      { stdio: ['pipe', 'ignore', 'pipe'] },
    );
    let stderr = '';
    decoder.stderr.on('data', (chunk: Buffer) => (stderr += chunk.toString()));
    encoder.stderr.on('data', (chunk: Buffer) => (stderr += chunk.toString()));
    const exit = (child: typeof decoder | typeof encoder): Promise<number | null> =>
      new Promise((resolve) => {
        child.on('error', (error) => {
          stderr += error.message;
          resolve(null);
        });
        child.on('close', resolve);
      });
    const decoded = exit(decoder);
    const encoded = exit(encoder);
    encoder.stdin.on('error', (error) => (stderr += error.message));

    const matrix = bayerMatrix(dither);
    const frameBytes = width * height;
    let pending: Buffer = Buffer.alloc(0);
    for await (const chunk of decoder.stdout as AsyncIterable<Buffer>) {
      if (encoder.stdin.destroyed) {
        decoder.kill();
        break;
      }
      pending = pending.length === 0 ? chunk : Buffer.concat([pending, chunk]);
      while (pending.length >= frameBytes && !encoder.stdin.destroyed) {
        const frame = Uint8Array.from(pending.subarray(0, frameBytes));
        pending = pending.subarray(frameBytes);
        ditherFrame(frame, width, matrix);
        if (!encoder.stdin.write(frame) && !encoder.stdin.destroyed) {
          await new Promise<void>((resolve) => {
            const done = (): void => {
              encoder.stdin.off('drain', done);
              encoder.stdin.off('close', done);
              resolve();
            };
            encoder.stdin.on('drain', done);
            encoder.stdin.on('close', done);
          });
        }
      }
    }
    encoder.stdin.end();

    const [decoderCode, encoderCode] = await Promise.all([decoded, encoded]);
    if (decoderCode === 0 && encoderCode === 0 && existsSync(outputPath) && statSync(outputPath).size > 0) return true;
    console.error(`${inputPath}: ffmpeg failed\n${stderr}`);
    return false;
  } catch (error) {
    console.error(`${inputPath}: ${(error as Error).message}`);
    return false;
  }
}

const { values } = parseArgs({
  options: {
    source: { type: 'string' },
    out: { type: 'string' },
    width: { type: 'string' },
    height: { type: 'string' },
    dither: { type: 'string', default: '4' },
    upscale: { type: 'string', default: '4' },
  },
});
if (values.source === undefined || values.out === undefined || values.width === undefined || values.height === undefined) {
  console.error('usage: tsx video-dither.ts --source <video or folder> --out <folder> --width <pixels> --height <pixels> [--dither 2|4|8] [--upscale 4]');
  process.exit(1);
}
const dither = Number(values.dither);
if (![2, 4, 8].includes(dither)) throw new Error(`--dither ${values.dither} is not 2, 4 or 8`);
const conversion: DitherConversion = { width: Number(values.width), height: Number(values.height), dither, upscale: Number(values.upscale) };
const source = resolve(values.source);
const outDir = resolve(values.out);
const inputs = statSync(source).isDirectory()
  ? readdirSync(source).sort().map((file) => join(source, file)).filter((file) => statSync(file).isFile())
  : [source];
mkdirSync(outDir, { recursive: true });

let converted = 0;
let failed = 0;
for (const input of inputs) {
  if (!(await hasVideoStream(input))) continue;
  const output = join(outDir, `${basename(input, extname(input))}.mp4`);
  if (await convertDitheredVideo(input, output, conversion)) {
    converted++;
    console.log(`ok      ${input} -> ${output}`);
  } else {
    failed++;
    console.log(`failed  ${input}`);
  }
}
console.log(`${converted} video(s) in ${outDir}`);
if (failed > 0) process.exit(1);
