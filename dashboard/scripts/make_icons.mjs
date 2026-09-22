// Draws the app icons with no image library: a minimal PNG encoder on
// node:zlib. Run by hand after changing the design, and commit the PNGs:
//
//   node scripts/make_icons.mjs        (from dashboard/, writes static/icon-*.png)
//
// The tile is fully opaque and square: iOS rounds the corners itself, and a
// transparent corner would render black on the home screen.
import { deflateSync } from 'node:zlib';
import { writeFileSync } from 'node:fs';

const CRC = new Uint32Array(256).map((_, n) => {
	let c = n;
	for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
	return c >>> 0;
});

function crc32(buf) {
	let c = 0xffffffff;
	for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
	return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
	const len = Buffer.alloc(4);
	len.writeUInt32BE(data.length);
	const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
	const crc = Buffer.alloc(4);
	crc.writeUInt32BE(crc32(td));
	return Buffer.concat([len, td, crc]);
}

function png(size, pixel) {
	const stride = size * 4 + 1;
	const raw = Buffer.alloc(size * stride);
	for (let y = 0; y < size; y++) {
		raw[y * stride] = 0; // filter: none
		for (let x = 0; x < size; x++) {
			const [r, g, b] = pixel(x, y);
			const o = y * stride + 1 + x * 4;
			raw[o] = r;
			raw[o + 1] = g;
			raw[o + 2] = b;
			raw[o + 3] = 255;
		}
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(size, 0);
	ihdr.writeUInt32BE(size, 4);
	ihdr[8] = 8; // bit depth
	ihdr[9] = 6; // RGBA
	return Buffer.concat([
		Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
		chunk('IHDR', ihdr),
		chunk('IDAT', deflateSync(raw, { level: 9 })),
		chunk('IEND', Buffer.alloc(0))
	]);
}

// The dashboard's own palette (src/app.css).
const BG = [10, 10, 15];
const CYAN = [0, 204, 221];
const PURPLE = [170, 102, 255];
const INK = [224, 224, 232];
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

// Colour at one sample point, in unit coordinates (0..1). A story ring (the
// app's signature element) around an "E".
function sample(u, v) {
	const dx = u - 0.5;
	const dy = v - 0.5;
	const r = Math.hypot(dx, dy);
	if (r > 0.33 && r < 0.39) {
		const t = (Math.atan2(dy, dx) / Math.PI + 1) / 2; // 0..1 around the ring
		return mix(CYAN, PURPLE, t < 0.5 ? t * 2 : (1 - t) * 2);
	}
	const spine = u > 0.4 && u < 0.465 && v > 0.365 && v < 0.635;
	const top = v > 0.365 && v < 0.43;
	const mid = v > 0.468 && v < 0.532;
	const bot = v > 0.57 && v < 0.635;
	const bars = u > 0.4 && ((top && u < 0.615) || (mid && u < 0.585) || (bot && u < 0.615));
	return spine || bars ? INK : BG;
}

function draw(size) {
	const S = 4; // 4x4 supersampling for smooth edges
	return png(size, (x, y) => {
		const acc = [0, 0, 0];
		for (let i = 0; i < S; i++) {
			for (let j = 0; j < S; j++) {
				const c = sample((x + (i + 0.5) / S) / size, (y + (j + 0.5) / S) / size);
				acc[0] += c[0];
				acc[1] += c[1];
				acc[2] += c[2];
			}
		}
		return acc.map((v) => Math.round(v / (S * S)));
	});
}

for (const size of [180, 192, 512]) {
	writeFileSync(new URL(`../static/icon-${size}.png`, import.meta.url), draw(size));
	console.log(`static/icon-${size}.png`);
}
