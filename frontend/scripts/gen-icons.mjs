// Generates simple "LL" PNG icons into public/. Not part of the build: run `node scripts/gen-icons.mjs`.
import { deflateSync } from "node:zlib";
import { writeFileSync } from "node:fs";

const BG = [37, 99, 235];
const FG = [255, 255, 255];

const crcTable = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
const chunk = (type, data) => {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const td = Buffer.concat([Buffer.from(type), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td));
  return Buffer.concat([len, td, crc]);
};

// scale: fraction of the canvas occupied by the glyphs (smaller for maskable safe zone).
function makePng(size, scale) {
  const raw = Buffer.alloc((size * 3 + 1) * size);
  const glyphH = size * scale;
  const stroke = glyphH * 0.2;
  const glyphW = glyphH * 0.6;
  const gap = glyphH * 0.15;
  const totalW = glyphW * 2 + gap;
  const x0 = (size - totalW) / 2;
  const y0 = (size - glyphH) / 2;
  const inL = (x, y, ox) =>
    y >= y0 &&
    y < y0 + glyphH &&
    x >= ox &&
    x < ox + glyphW &&
    (x < ox + stroke || y >= y0 + glyphH - stroke);
  for (let y = 0; y < size; y++) {
    const row = y * (size * 3 + 1);
    raw[row] = 0;
    for (let x = 0; x < size; x++) {
      const on = inL(x, y, x0) || inL(x, y, x0 + glyphW + gap);
      const c = on ? FG : BG;
      raw.set(c, row + 1 + x * 3);
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

writeFileSync("public/icon-192.png", makePng(192, 0.6));
writeFileSync("public/icon-512.png", makePng(512, 0.6));
writeFileSync("public/icon-512-maskable.png", makePng(512, 0.4));
writeFileSync("public/apple-touch-icon.png", makePng(180, 0.6));
