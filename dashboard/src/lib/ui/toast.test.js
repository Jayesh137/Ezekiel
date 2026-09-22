// src/lib/ui/toast.test.js
// The toast queue carries Undo for every review mark, so a lost or double-run
// action is a mark the owner cannot take back.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { toasts, toast, dismiss, runAction, MAX_TOASTS } from './toast.js';

function current() {
	let v;
	toasts.subscribe((x) => (v = x))();
	return v;
}

beforeEach(() => {
	for (const t of current()) dismiss(t.id);
});

test('toast queues newest last and caps at MAX_TOASTS, evicting the oldest', () => {
	for (let i = 0; i < MAX_TOASTS + 2; i++) toast(`m${i}`, { duration: 0 });
	const list = current();
	assert.equal(list.length, MAX_TOASTS);
	assert.deepEqual(list.map((t) => t.message), ['m2', 'm3', 'm4']);
});

test('dismiss removes one toast and ignores unknown ids', () => {
	const a = toast('a', { duration: 0 });
	toast('b', { duration: 0 });
	dismiss(a);
	dismiss(9999);
	assert.deepEqual(current().map((t) => t.message), ['b']);
});

test('runAction runs the callback exactly once and dismisses', () => {
	let n = 0;
	const id = toast('undoable', { duration: 0, action: { label: 'Undo', run: () => n++ } });
	runAction(id);
	runAction(id);
	assert.equal(n, 1);
	assert.equal(current().length, 0);
});

test('subscribers get the current list at once and on every change', () => {
	const seen = [];
	const unsubscribe = toasts.subscribe((v) => seen.push(v.map((t) => t.message)));
	const id = toast('x', { duration: 0 });
	dismiss(id);
	unsubscribe();
	toast('after', { duration: 0 });
	assert.deepEqual(seen, [[], ['x'], []]);
});

test('a timed toast dismisses itself', async () => {
	toast('brief', { duration: 5 });
	assert.equal(current().length, 1);
	await new Promise((r) => setTimeout(r, 30));
	assert.equal(current().length, 0);
});
