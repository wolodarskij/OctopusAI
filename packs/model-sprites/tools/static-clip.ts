// Adds the clip `static` to a GLB that has no animation, such as a Tripo tree: one key at time 0 that holds the first
// root node where it stands. `render-frames.ts --clips static:1:once` then renders the model in the 8 view
// facings, one frame each.
//
//   tsx static-clip.ts <model.glb> <out.glb>

import { readFileSync, writeFileSync } from 'node:fs';

const GLB_MAGIC = 0x46546c67;
const JSON_CHUNK = 0x4e4f534a;
const BIN_CHUNK = 0x004e4942;
const FLOAT = 5126;

interface Gltf {
  scenes: { nodes: number[] }[];
  nodes: { translation?: number[] }[];
  buffers: { byteLength: number }[];
  bufferViews: object[];
  accessors: object[];
  animations?: object[];
}

const [input, output] = process.argv.slice(2);
if (input === undefined || output === undefined) {
  console.error('usage: tsx static-clip.ts <model.glb> <out.glb>');
  process.exit(1);
}

const glb = readFileSync(input);
if (glb.readUInt32LE(0) !== GLB_MAGIC) throw new Error(`${input} is not a GLB file`);
const jsonLength = glb.readUInt32LE(12);
if (glb.readUInt32LE(16) !== JSON_CHUNK) throw new Error(`${input} has no JSON chunk first`);
const gltf = JSON.parse(glb.subarray(20, 20 + jsonLength).toString('utf8')) as Gltf;
const binStart = 20 + jsonLength;
if (glb.readUInt32LE(binStart + 4) !== BIN_CHUNK) throw new Error(`${input} has no binary chunk`);
const bin = glb.subarray(binStart + 8, binStart + 8 + glb.readUInt32LE(binStart));
if ((gltf.animations?.length ?? 0) > 0) throw new Error(`${input} has animations already`);

const node = gltf.scenes[0]?.nodes[0];
if (node === undefined) throw new Error(`${input} has no root node`);
const translation = gltf.nodes[node]?.translation ?? [0, 0, 0];
const keys = Buffer.alloc(16);
keys.writeFloatLE(0, 0);
translation.forEach((value, index) => keys.writeFloatLE(value, 4 + index * 4));

const offset = bin.length;
const view = gltf.bufferViews.length;
gltf.bufferViews.push({ buffer: 0, byteOffset: offset, byteLength: 4 }, { buffer: 0, byteOffset: offset + 4, byteLength: 12 });
const accessor = gltf.accessors.length;
gltf.accessors.push(
  { bufferView: view, componentType: FLOAT, count: 1, type: 'SCALAR', min: [0], max: [0] },
  { bufferView: view + 1, componentType: FLOAT, count: 1, type: 'VEC3' },
);
gltf.animations = [{
  name: 'static',
  samplers: [{ input: accessor, output: accessor + 1, interpolation: 'STEP' }],
  channels: [{ sampler: 0, target: { node, path: 'translation' } }],
}];
const newBin = Buffer.concat([bin, keys]);
const buffer = gltf.buffers[0];
if (buffer === undefined) throw new Error(`${input} has no buffer`);
buffer.byteLength = newBin.length;

const pad = (data: Buffer, fill: number): Buffer => Buffer.concat([data, Buffer.alloc((4 - (data.length % 4)) % 4, fill)]);
const json = pad(Buffer.from(JSON.stringify(gltf), 'utf8'), 0x20);
const binary = pad(newBin, 0);
const header = Buffer.alloc(12);
header.writeUInt32LE(GLB_MAGIC, 0);
header.writeUInt32LE(2, 4);
header.writeUInt32LE(12 + 8 + json.length + 8 + binary.length, 8);
const chunk = (type: number, data: Buffer): Buffer => {
  const head = Buffer.alloc(8);
  head.writeUInt32LE(data.length, 0);
  head.writeUInt32LE(type, 4);
  return Buffer.concat([head, data]);
};
writeFileSync(output, Buffer.concat([header, chunk(JSON_CHUNK, json), chunk(BIN_CHUNK, binary)]));
console.log(`written: ${output}, with the clip static`);
