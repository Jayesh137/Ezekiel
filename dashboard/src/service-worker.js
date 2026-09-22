/// <reference types="@sveltejs/kit" />
// App-shell cache ONLY (dashboard/ARCHITECTURE.md §7.5). The app opens
// instantly and works on a poor signal, but data is never cached: requests to
// raw.githubusercontent.com are cross-origin and pass straight through, because
// a cached roster shown offline would present a stale list as the current one.
import { base, build, files, prerendered, version } from '$service-worker';

const CACHE = `ezekiel-shell-${version}`;
const SHELL = [...build, ...files, ...prerendered];
const SHELL_SET = new Set(SHELL);

self.addEventListener('install', (event) => {
	event.waitUntil(
		caches.open(CACHE)
			.then((cache) => cache.addAll(SHELL))
			.then(() => self.skipWaiting())
	);
});

self.addEventListener('activate', (event) => {
	event.waitUntil(
		caches.keys()
			.then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
			.then(() => self.clients.claim())
	);
});

self.addEventListener('fetch', (event) => {
	const req = event.request;
	if (req.method !== 'GET') return;
	const url = new URL(req.url);
	// Data, Hypurrscan, fonts: anything off-origin is never touched.
	if (url.origin !== self.location.origin) return;

	if (SHELL_SET.has(url.pathname) && req.mode !== 'navigate') {
		// Versioned, immutable build output: cache first.
		event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
		return;
	}
	if (req.mode === 'navigate') {
		// Network first so a new deploy is picked up at once; the cached page
		// only when the network is gone.
		event.respondWith(
			fetch(req).catch(async () =>
				(await caches.match(url.pathname))
				|| (await caches.match(`${base}/review`))
				|| Response.error()
			)
		);
	}
});
