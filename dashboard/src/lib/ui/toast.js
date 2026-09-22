// src/lib/ui/toast.js
// Toast queue. Implements the Svelte store contract by hand (subscribe calls
// back at once and returns an unsubscribe), so it has no import and runs under
// node --test.

export const MAX_TOASTS = 3;
const DEFAULT_MS = 4000;

let list = [];
let nextId = 1;
const subs = new Set();
const timers = new Map();

function emit() {
	for (const fn of subs) fn(list);
}

function clearTimer(id) {
	const t = timers.get(id);
	if (t) clearTimeout(t);
	timers.delete(id);
}

export const toasts = {
	subscribe(fn) {
		subs.add(fn);
		fn(list);
		return () => subs.delete(fn);
	}
};

export function dismiss(id) {
	clearTimer(id);
	const next = list.filter((x) => x.id !== id);
	if (next.length === list.length) return;
	list = next;
	emit();
}

/** Show a toast. duration 0 keeps it until dismissed. Returns its id. */
export function toast(message, { action = null, duration = DEFAULT_MS } = {}) {
	const id = nextId++;
	let next = [...list, { id, message, action }];
	while (next.length > MAX_TOASTS) {
		clearTimer(next[0].id);
		next = next.slice(1);
	}
	list = next;
	emit();
	if (duration > 0) timers.set(id, setTimeout(() => dismiss(id), duration));
	return id;
}

/** Run a toast's action once and dismiss it. A second call is a no-op. */
export function runAction(id) {
	const t = list.find((x) => x.id === id);
	if (!t) return;
	dismiss(id);
	t.action?.run?.();
}
