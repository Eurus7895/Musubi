// Dependency-free icon generator: rasterizes the Musubi woven-knot logomark
// (the same 40×40 paths used in the activity bar) onto dark tiles and writes
// PNGs Tauri can bundle. For .ico/.icns run: `npm run tauri icon icons/icon.png`.
import { deflateSync } from 'node:zlib'
import { writeFileSync, mkdirSync } from 'node:fs'

const BG = [13, 17, 23, 255]       // #0d1117
const WHITE = [233, 233, 234, 255] // #e9e9ea
const AMBER = [255, 155, 61, 255]  // #ff9b3d

// segments in the 40×40 logo space: [x1,y1,x2,y2,color]
const SEGS = [
  [6, 25, 22, 25, WHITE], [28, 25, 34, 25, WHITE],
  [6, 15, 12, 15, WHITE], [18, 15, 34, 15, WHITE],
  [25, 6, 25, 12, WHITE], [25, 18, 25, 34, WHITE],
  [15, 6, 15, 22, AMBER], [15, 28, 15, 34, AMBER],
]

function render(N) {
  const buf = Buffer.alloc(N * N * 4)
  for (let i = 0; i < N * N; i++) buf.set(BG, i * 4)
  const s = N / 40
  const half = (2.8 * s) / 2
  const put = (x, y, c) => {
    const xi = Math.round(x), yi = Math.round(y)
    if (xi < 0 || yi < 0 || xi >= N || yi >= N) return
    buf.set(c, (yi * N + xi) * 4)
  }
  for (const [x1, y1, x2, y2, c] of SEGS) {
    const ax = x1 * s, ay = y1 * s, bx = x2 * s, by = y2 * s
    const x0 = Math.min(ax, bx) - half, x3 = Math.max(ax, bx) + half
    const yy0 = Math.min(ay, by) - half, yy3 = Math.max(ay, by) + half
    for (let y = Math.floor(yy0); y <= Math.ceil(yy3); y++)
      for (let x = Math.floor(x0); x <= Math.ceil(x3); x++) put(x, y, c)
  }
  return buf
}

// ── minimal PNG encoder (RGBA, no filtering) ──
function crc32(buf) {
  let c = ~0
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i]
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
  }
  return (~c) >>> 0
}
function chunk(type, data) {
  const t = Buffer.from(type, 'ascii')
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([t, data])))
  return Buffer.concat([len, t, data, crc])
}
function png(N, rgba) {
  const sig = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(N, 0); ihdr.writeUInt32BE(N, 4)
  ihdr[8] = 8; ihdr[9] = 6 // 8-bit, RGBA
  const raw = Buffer.alloc(N * (N * 4 + 1))
  for (let y = 0; y < N; y++) {
    raw[y * (N * 4 + 1)] = 0 // filter: none
    rgba.copy(raw, y * (N * 4 + 1) + 1, y * N * 4, (y + 1) * N * 4)
  }
  return Buffer.concat([sig, chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}

const dir = new URL('./icons/', import.meta.url)
mkdirSync(dir, { recursive: true })
// icon.png is the 1024px source `tauri icon` derives .ico/.icns from in CI.
const out = { '32x32.png': 32, '128x128.png': 128, '128x128@2x.png': 256, 'icon.png': 1024 }
for (const [name, size] of Object.entries(out)) {
  writeFileSync(new URL(name, dir), png(size, render(size)))
  console.log('wrote icons/' + name + ' (' + size + 'px)')
}
