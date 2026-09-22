// Asserts the build is installable. Run after `npm run build`:
//   npm run check:pwa
// A PWA that silently stopped being installable looks fine in every desktop
// check, so this is the one place that notices.
import { existsSync, readFileSync, readdirSync } from 'node:fs';

const problems = [];
const need = ['manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png', 'service-worker.js'];
for (const f of need) {
	if (!existsSync(`build/${f}`)) problems.push(`missing build/${f}`);
}

if (existsSync('build/manifest.webmanifest')) {
	const m = JSON.parse(readFileSync('build/manifest.webmanifest', 'utf8'));
	if (!m.start_url?.startsWith('/Ezekiel/')) problems.push(`start_url lacks the /Ezekiel base: ${m.start_url}`);
	if (m.display !== 'standalone') problems.push(`display is ${m.display}, not standalone`);
	for (const icon of m.icons || []) {
		const file = icon.src.replace(/^\/Ezekiel\//, '');
		if (!existsSync(`build/${file}`)) problems.push(`manifest icon not in build: ${icon.src}`);
	}
}

const pages = readdirSync('build').filter((f) => f.endsWith('.html'));
if (!pages.includes('review.html')) problems.push('build/review.html was not prerendered');
for (const page of ['index.html', 'review.html']) {
	if (!existsSync(`build/${page}`)) continue;
	const html = readFileSync(`build/${page}`, 'utf8');
	for (const tag of ['rel="manifest"', 'apple-touch-icon', 'viewport-fit=cover', 'apple-mobile-web-app-capable']) {
		if (!html.includes(tag)) problems.push(`${page} lacks ${tag}`);
	}
}

if (problems.length) {
	for (const p of problems) console.error(`PWA: ${p}`);
	process.exit(1);
}
console.log(`PWA build OK (${need.length} files, manifest, ${pages.length} pages carry the tags)`);
