# Ezekiel Review Phone App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only, installable (PWA) phone app at `/Ezekiel/review` that shows the identified wallets as a social-style feed with a gamified review queue, where every address opens its Hypurrscan wallet page.

**Architecture:** A new SvelteKit route inside the existing `dashboard/`. It fetches the committed `data/roster/latest.json` and `data/watchlist/latest.json` at open time, with no backend. All rules live in the pure module `src/lib/review.js`; a dependency-free UI kit lives in `src/lib/ui/`; review marks persist in `localStorage`.

**Tech Stack:** Svelte 5 (legacy syntax, matching existing pages), SvelteKit 2, Vite 6, adapter-static, `node --test`, GitHub Pages.

**Spec:** `dashboard/ARCHITECTURE.md`

## Global Constraints

- No new npm dependencies. No Tailwind, no animation library.
- Svelte legacy syntax (`export let`, `$:`, `on:click`, `<slot/>`, `createEventDispatcher`), matching existing pages.
- Every address link goes through `addressUrl()` from `src/lib/api.js`, never an inline URL.
- A missing figure renders `—`, never `$0` or `0` (project rule 6).
- A failed read must never serialise as a clean result, and review state is written only after a successful roster load (rule 5).
- Motion: none on frequent actions (tabs, mark reviewed, expand); ≤300ms, `cubic-bezier(0.23, 1, 0.32, 1)` elsewhere; zero under `prefers-reduced-motion`.
- Touch targets ≥44px, and `tabular-nums` on figures.
- Tests are network-free and never write to real `data/`.
- Base path is `/Ezekiel` in builds and `''` in dev (`svelte.config.js`).
- Run all commands from `dashboard/` unless stated otherwise.

---

### Task 1: Feed logic (`review.js` part 1)

**Files:**
- Create: `dashboard/src/lib/review.js`
- Create: `dashboard/src/lib/review.test.js`
- Modify: `dashboard/package.json` (the test script glob already covers `src/lib/*.test.js`; no change needed, just verify)
- Modify: `dashboard/src/routes/roster/+page.svelte` (import `VECTOR_LABEL` instead of its local copy)

**Interfaces:**
- Produces: `TIER_RANK`, `REVIEW_TIERS`, `SIZE_BAND`, `TIER_LABEL`, `VECTOR_LABEL`, `vectorLabel(v)`, `walletKey(addr)`, `eligible(roster)`, `status(w, state)`, `feed(roster, state, tab)`, `tabCounts(roster, state)`, `emptyState()`, `avatar(address) → {a, b, angle}`, `freshness(iso, nowMs) → {minutes, level}`, `ago(ms, nowMs)`, `ratio(x)`, `bornLabel(ms)`

- [ ] **Step 1: Write the failing tests** in `src/lib/review.test.js`:

```js
// src/lib/review.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	eligible, feed, tabCounts, status, emptyState, vectorLabel, avatar,
	freshness, ago, ratio, bornLabel
} from './review.js';

const T = '0x45d26f28196d226497130c4bac709d808fed4029';
const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);
const D = '0x' + 'd'.repeat(40);

function w(wallet, tier, extra = {}) {
	return { wallet, tier, vectors: [], vector_count: 0, rank_strength: 0.1,
		is_service: false, reasons: [], evidence: {}, ...extra };
}

test('eligible drops infrastructure, services and the target', () => {
	const roster = { target: T, wallets: [
		w(A, 'POSSIBLE'), w(B, 'INFRASTRUCTURE'), w(C, 'WATCH', { is_service: true }),
		w(T.toUpperCase().replace('0X', '0x'), 'CONFIRMED')
	] };
	assert.deepEqual(eligible(roster).map((x) => x.wallet), [A]);
});

test('eligible tolerates a missing roster', () => {
	assert.deepEqual(eligible(null), []);
	assert.deepEqual(eligible({}), []);
});

test('status: new, reviewed, changed', () => {
	const s = { ...emptyState(), reviewed: { [A]: 'POSSIBLE', [B]: 'PROBABLE' } };
	assert.equal(status(w(C, 'POSSIBLE'), s), 'new');
	assert.equal(status(w(A, 'POSSIBLE'), s), 'reviewed');
	assert.equal(status(w(B, 'POSSIBLE'), s), 'changed');
});

test('status keys on the lowercased address', () => {
	const s = { ...emptyState(), reviewed: { [A]: 'POSSIBLE' } };
	assert.equal(status(w(A.toUpperCase().replace('0X', '0x'), 'POSSIBLE'), s), 'reviewed');
});

test('review tab: C/P/P plus any changed wallet, including a demotion to WATCH', () => {
	const roster = { target: T, wallets: [
		w(A, 'POSSIBLE'), w(B, 'WATCH'), w(C, 'WATCH'), w(D, 'CONFIRMED')
	] };
	const s = { ...emptyState(), reviewed: { [B]: 'POSSIBLE', [C]: 'WATCH' } };
	const got = feed(roster, s, 'review').map((x) => x.wallet);
	assert.ok(got.includes(A) && got.includes(D) && got.includes(B));
	assert.ok(!got.includes(C), 'an unchanged WATCH wallet is not in the review tab');
});

test('watch and all tabs', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'WATCH')] };
	assert.deepEqual(feed(roster, emptyState(), 'watch').map((x) => x.wallet), [B]);
	assert.equal(feed(roster, emptyState(), 'all').length, 2);
});

test('order: unread, tier, vector count, strength (absent last), address', () => {
	const roster = { target: T, wallets: [
		w(A, 'POSSIBLE', { vector_count: 1, rank_strength: 0.9 }),
		w(B, 'CONFIRMED', { vector_count: 2 }),
		w(C, 'POSSIBLE', { vector_count: 1, rank_strength: null }),
		w(D, 'POSSIBLE', { vector_count: 2, rank_strength: 0.2 })
	] };
	// B was reviewed at its current tier, so it is read and sorts after unread.
	const s = { ...emptyState(), reviewed: { [B]: 'CONFIRMED' } };
	assert.deepEqual(feed(roster, s, 'review').map((x) => x.wallet), [D, A, C, B]);
});

test('feed rows carry their status', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE')] };
	assert.equal(feed(roster, emptyState(), 'review')[0].status, 'new');
});

test('tabCounts', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'WATCH'), w(C, 'INFRASTRUCTURE')] };
	assert.deepEqual(tabCounts(roster, emptyState()), { review: 1, watch: 1, all: 2 });
});

test('vectorLabel: known names in plain English, unknown raw', () => {
	assert.equal(vectorLabel('shared_agent'), 'Shares an agent');
	assert.equal(vectorLabel('something_new'), 'something_new');
});

test('avatar is deterministic and case-insensitive', () => {
	assert.deepEqual(avatar(A), avatar(A.toUpperCase().replace('0X', '0x')));
	assert.notDeepEqual(avatar(A), avatar(B));
	const { a, b, angle } = avatar(A);
	for (const v of [a, b, angle]) assert.ok(v >= 0 && v < 360);
});

test('freshness thresholds', () => {
	const now = Date.parse('2026-09-22T12:00:00Z');
	const at = (m) => new Date(now - m * 60000).toISOString();
	assert.equal(freshness(at(10), now).level, 'ok');
	assert.equal(freshness(at(160), now).level, 'ok');
	assert.equal(freshness(at(161), now).level, 'warn');
	assert.equal(freshness(at(361), now).level, 'stale');
	assert.deepEqual(freshness(null, now), { minutes: null, level: 'unknown' });
	assert.deepEqual(freshness('garbage', now), { minutes: null, level: 'unknown' });
});

test('ago', () => {
	const now = 10 * 86400000;
	assert.equal(ago(null, now), '—');
	assert.equal(ago(now - 30000, now), 'just now');
	assert.equal(ago(now - 5 * 60000, now), '5m ago');
	assert.equal(ago(now - 3 * 3600000, now), '3h ago');
	assert.equal(ago(now - 3 * 86400000, now), '3d ago');
});

test('ratio and bornLabel never invent a number', () => {
	assert.equal(ratio(null), '—');
	assert.equal(ratio(Number.NaN), '—');
	assert.equal(ratio(0.6942), '0.69x');
	assert.equal(ratio(0), '0.00x');
	assert.equal(bornLabel(null), '—');
	assert.equal(bornLabel(Date.parse('2025-01-01T20:28:00Z')), 'Jan 2025');
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test`
Expected: FAIL with `Cannot find module ... review.js`

- [ ] **Step 3: Implement `src/lib/review.js` (part 1)**

```js
// src/lib/review.js
// Every decision the phone review app makes, as pure functions over plain
// objects. Components render; this module decides. See dashboard/ARCHITECTURE.md §6.

export const TIER_RANK = { CONFIRMED: 0, PROBABLE: 1, POSSIBLE: 2, WATCH: 3 };
export const REVIEW_TIERS = ['CONFIRMED', 'PROBABLE', 'POSSIBLE'];
/** The watch's own outgrew-target band (check_watchlist). */
export const SIZE_BAND = 1.15;

export const TIER_LABEL = {
	CONFIRMED: 'Confirmed',
	PROBABLE: 'Probable',
	POSSIBLE: 'Possible',
	WATCH: 'Watch',
	INFRASTRUCTURE: 'Service'
};

// Every vector src/roster.py defines. Each says what was OBSERVED, not a score.
export const VECTOR_LABEL = {
	transfer: 'Observed transfer',
	linkage: 'Shared funder / deposit address',
	correlation: 'Exit amount re-appeared as a deposit',
	behavioural: 'Trades like the target',
	hl_native: 'Two-way flow inside Hyperliquid',
	shared_agent: 'Shares an agent',
	explicit_link: 'Explicit link (sub-account / role)',
	dormancy_handoff: 'Born inside his silence',
	referral: 'Referral link'
};

/** @param {string} v */
export function vectorLabel(v) {
	return VECTOR_LABEL[v] || v;
}

/** @param {string} addr */
export function walletKey(addr) {
	return String(addr || '').toLowerCase();
}

export function emptyState() {
	return { reviewed: {}, stories: {}, streak: null };
}

/** Wallets the app may show at all: no services, no infrastructure, not him. */
export function eligible(roster) {
	const target = walletKey(roster?.target);
	return (roster?.wallets || []).filter(
		(w) => w && w.wallet && w.tier !== 'INFRASTRUCTURE' && !w.is_service
			&& walletKey(w.wallet) !== target
	);
}

/** 'new' | 'changed' | 'reviewed', from the tier you last reviewed it at. */
export function status(w, state) {
	const seen = state?.reviewed?.[walletKey(w.wallet)];
	if (seen === undefined) return 'new';
	return seen === w.tier ? 'reviewed' : 'changed';
}

function strength(w) {
	const v = w.rank_strength;
	return typeof v === 'number' && Number.isFinite(v) ? v : -Infinity;
}

function compare(x, y) {
	const ux = x.status === 'reviewed' ? 1 : 0;
	const uy = y.status === 'reviewed' ? 1 : 0;
	if (ux !== uy) return ux - uy;
	const tx = TIER_RANK[x.tier] ?? 9;
	const ty = TIER_RANK[y.tier] ?? 9;
	if (tx !== ty) return tx - ty;
	const vx = x.vector_count || 0;
	const vy = y.vector_count || 0;
	if (vx !== vy) return vy - vx;
	const sx = strength(x);
	const sy = strength(y);
	if (sx !== sy) return sy > sx ? 1 : -1;
	return walletKey(x.wallet) < walletKey(y.wallet) ? -1 : 1;
}

const TAB_FILTER = {
	review: (r) => REVIEW_TIERS.includes(r.tier) || r.status === 'changed',
	watch: (r) => r.tier === 'WATCH',
	all: () => true
};

/** The rows for one tab, each carrying its `status`, in feed order. */
export function feed(roster, state, tab = 'review') {
	const pick = TAB_FILTER[tab] || TAB_FILTER.review;
	return eligible(roster)
		.map((w) => ({ ...w, status: status(w, state) }))
		.filter(pick)
		.sort(compare);
}

export function tabCounts(roster, state) {
	return {
		review: feed(roster, state, 'review').length,
		watch: feed(roster, state, 'watch').length,
		all: feed(roster, state, 'all').length
	};
}

/** Deterministic gradient identicon: FNV-1a over the lowercased address. */
export function avatar(address) {
	let h = 0x811c9dc5;
	for (const ch of walletKey(address)) {
		h ^= ch.charCodeAt(0);
		h = Math.imul(h, 0x01000193) >>> 0;
	}
	const a = h % 360;
	const b = (a + 40 + ((h >>> 9) % 80)) % 360;
	const angle = (h >>> 17) % 360;
	return { a, b, angle };
}

/** Same thresholds as +layout.svelte's collection pill. */
export function freshness(iso, nowMs = Date.now()) {
	const t = iso ? Date.parse(iso) : NaN;
	if (!Number.isFinite(t)) return { minutes: null, level: 'unknown' };
	const minutes = Math.max(0, Math.round((nowMs - t) / 60000));
	const level = minutes <= 160 ? 'ok' : minutes <= 360 ? 'warn' : 'stale';
	return { minutes, level };
}

export function ago(ms, nowMs = Date.now()) {
	if (ms == null || !Number.isFinite(ms)) return '—';
	const s = Math.max(0, (nowMs - ms) / 1000);
	if (s < 60) return 'just now';
	if (s < 3600) return `${Math.floor(s / 60)}m ago`;
	if (s < 48 * 3600) return `${Math.floor(s / 3600)}h ago`;
	return `${Math.floor(s / 86400)}d ago`;
}

export function ratio(x) {
	return typeof x === 'number' && Number.isFinite(x) ? `${x.toFixed(2)}x` : '—';
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function bornLabel(ms) {
	if (ms == null || !Number.isFinite(ms)) return '—';
	const d = new Date(ms);
	return `${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
```

- [ ] **Step 4: Run tests.** Run `npm test`. Expected: all review tests PASS, and the existing api tests still PASS.

- [ ] **Step 5: Roster page uses the shared labels.** In `src/routes/roster/+page.svelte`, delete the local `const VECTOR_LABEL = {…};` block and add `import { VECTOR_LABEL } from '$lib/review.js';` beside the other imports.

- [ ] **Step 6: Build check.** Run `npm run build`. Expected: success.

- [ ] **Step 7: Commit**

```bash
git add src/lib/review.js src/lib/review.test.js src/routes/roster/+page.svelte
git commit -m "feat(review): feed selection, order and formatting logic"
```

---

### Task 2: Review state (`review.js` part 2)

**Files:**
- Modify: `dashboard/src/lib/review.js` (append)
- Modify: `dashboard/src/lib/review.test.js` (append)

**Interfaces:**
- Consumes: `walletKey`, `eligible`, `emptyState`, `SIZE_BAND` (Task 1)
- Produces: `STORAGE_KEY`, `markReviewed(state, rows)`, `dropped(roster, state) → [{wallet, tier}]`, `dismissDropped(state, wallet)`, `progress(rows) → {done,total,fresh,changed}`, `localDay(date)`, `checkIn(streak, today) → {day,count}`, `storyKey(w)`, `storyRing(w, state) → 'alert'|'unseen'|'seen'`, `markStorySeen(state, w)`, `watchFreshIso(watchlist)`, `readState(storage) → {state, ok}`, `writeState(storage, state) → boolean`

- [ ] **Step 1: Append the failing tests**

```js
import {
	markReviewed, dropped, dismissDropped, progress, localDay, checkIn,
	storyKey, storyRing, markStorySeen, watchFreshIso, readState, writeState, STORAGE_KEY
} from './review.js';

test('markReviewed records current tier and does not mutate', () => {
	const s0 = emptyState();
	const s1 = markReviewed(s0, [w(A, 'POSSIBLE'), w(B.toUpperCase().replace('0X', '0x'), 'CONFIRMED')]);
	assert.deepEqual(s0.reviewed, {});
	assert.deepEqual(s1.reviewed, { [A]: 'POSSIBLE', [B]: 'CONFIRMED' });
});

test('dropped: reviewed wallets that left the eligible set', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'INFRASTRUCTURE')] };
	const s = { ...emptyState(), reviewed: { [A]: 'POSSIBLE', [B]: 'WATCH', [C]: 'POSSIBLE' } };
	assert.deepEqual(dropped(roster, s), [{ wallet: B, tier: 'WATCH' }, { wallet: C, tier: 'POSSIBLE' }]);
	assert.deepEqual(Object.keys(dismissDropped(s, C).reviewed).sort(), [A, B]);
});

test('progress over feed rows', () => {
	const rows = [
		{ status: 'new' }, { status: 'changed' }, { status: 'reviewed' }, { status: 'reviewed' }
	];
	assert.deepEqual(progress(rows), { done: 2, total: 4, fresh: 1, changed: 1 });
	assert.deepEqual(progress([]), { done: 0, total: 0, fresh: 0, changed: 0 });
});

test('localDay uses the local calendar', () => {
	assert.equal(localDay(new Date(2026, 8, 2, 23, 59)), '2026-09-02');
});

test('checkIn: first, same day, next day, gap, month and year boundary', () => {
	assert.deepEqual(checkIn(null, '2026-09-22'), { day: '2026-09-22', count: 1 });
	assert.deepEqual(checkIn({ day: '2026-09-22', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 4 });
	assert.deepEqual(checkIn({ day: '2026-09-21', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 5 });
	assert.deepEqual(checkIn({ day: '2026-09-19', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 1 });
	assert.deepEqual(checkIn({ day: '2026-09-30', count: 2 }, '2026-10-01'), { day: '2026-10-01', count: 3 });
	assert.deepEqual(checkIn({ day: '2026-12-31', count: 2 }, '2027-01-01'), { day: '2027-01-01', count: 3 });
	assert.deepEqual(checkIn({ day: 'junk', count: 9 }, '2026-09-22'), { day: '2026-09-22', count: 1 });
});

test('stories: alert beats unseen beats seen; last fill is not in the key', () => {
	const base = { address: A, read_ok: true, agents: ['x'], subaccounts: [], size_ratio: 0.7, last_fill_ms: 1 };
	assert.equal(storyRing(base, emptyState()), 'unseen');
	const seen = markStorySeen(emptyState(), base);
	assert.equal(storyRing(base, seen), 'seen');
	assert.equal(storyRing({ ...base, last_fill_ms: 999 }, seen), 'seen');
	assert.equal(storyRing({ ...base, agents: ['x', 'y'] }, seen), 'unseen');
	assert.equal(storyRing({ ...base, size_ratio: 1.15 }, seen), 'alert');
	assert.equal(storyRing({ ...base, read_ok: false }, seen), 'alert');
	assert.equal(storyKey(base), '1|0|0|1');
});

test('watchFreshIso: computed_at, else newest checked_at, else null', () => {
	assert.equal(watchFreshIso({ computed_at: 'X' }), 'X');
	assert.equal(watchFreshIso({ wallets: [{ checked_at: '2026-09-01T00:00:00Z' }, { checked_at: '2026-09-02T00:00:00Z' }] }), '2026-09-02T00:00:00Z');
	assert.equal(watchFreshIso(null), null);
});

function fakeStorage(initial = {}) {
	const m = new Map(Object.entries(initial));
	return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m };
}

test('readState: absent, valid, corrupt, unavailable', () => {
	assert.deepEqual(readState(fakeStorage()), { state: emptyState(), ok: true });
	const good = { reviewed: { [A]: 'POSSIBLE' }, stories: {}, streak: { day: '2026-09-22', count: 2 } };
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: JSON.stringify(good) })).state, good);
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: '{nope' })), { state: emptyState(), ok: true });
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: '[1,2]' })).state, emptyState());
	assert.deepEqual(readState(null), { state: emptyState(), ok: false });
	const throwing = { getItem() { throw new Error('denied'); } };
	assert.deepEqual(readState(throwing), { state: emptyState(), ok: false });
});

test('writeState round-trips and reports failure', () => {
	const st = fakeStorage();
	const s = markReviewed(emptyState(), [w(A, 'POSSIBLE')]);
	assert.equal(writeState(st, s), true);
	assert.deepEqual(readState(st).state, s);
	assert.equal(writeState(null, s), false);
	assert.equal(writeState({ setItem() { throw new Error('quota'); } }, s), false);
});
```

- [ ] **Step 2: Run to verify failure.** `npm test`, expecting FAIL with `markReviewed is not exported` (or similar).

- [ ] **Step 3: Append the implementation to `src/lib/review.js`**

```js
// --- review state ------------------------------------------------------------

export const STORAGE_KEY = 'ezekiel.review.v1';

export function markReviewed(state, rows) {
	const reviewed = { ...(state?.reviewed || {}) };
	for (const r of rows) reviewed[walletKey(r.wallet)] = r.tier;
	return { ...state, reviewed };
}

/** Reviewed wallets no longer eligible (gone, or became a service). */
export function dropped(roster, state) {
	const live = new Set(eligible(roster).map((w) => walletKey(w.wallet)));
	return Object.entries(state?.reviewed || {})
		.filter(([k]) => !live.has(k))
		.map(([wallet, tier]) => ({ wallet, tier }))
		.sort((x, y) => (x.wallet < y.wallet ? -1 : 1));
}

export function dismissDropped(state, wallet) {
	const reviewed = { ...(state?.reviewed || {}) };
	delete reviewed[walletKey(wallet)];
	return { ...state, reviewed };
}

export function progress(rows) {
	let done = 0, fresh = 0, changed = 0;
	for (const r of rows) {
		if (r.status === 'reviewed') done++;
		else if (r.status === 'changed') changed++;
		else fresh++;
	}
	return { done, total: rows.length, fresh, changed };
}

// --- streak ------------------------------------------------------------------

export function localDay(date = new Date()) {
	const p = (n) => String(n).padStart(2, '0');
	return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())}`;
}

function dayAfter(day) {
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day || '');
	if (!m) return null;
	const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + 1));
	return d.toISOString().slice(0, 10);
}

/** Moves only on a successful load: opening offline shows you nothing. */
export function checkIn(streak, today) {
	if (streak?.day === today) return streak;
	if (streak && dayAfter(streak.day) === today) return { day: today, count: (streak.count || 0) + 1 };
	return { day: today, count: 1 };
}

// --- stories -----------------------------------------------------------------

/** last_fill_ms is deliberately absent: it changes every run, so a ring keyed
 *  on it would always be lit, and an always-lit ring means nothing. */
export function storyKey(w) {
	const big = typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND ? 1 : 0;
	return `${(w.agents || []).length}|${(w.subaccounts || []).length}|${big}|${w.read_ok ? 1 : 0}`;
}

export function storyRing(w, state) {
	if (!w.read_ok || (typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND)) return 'alert';
	return state?.stories?.[walletKey(w.address)] === storyKey(w) ? 'seen' : 'unseen';
}

export function markStorySeen(state, w) {
	return { ...state, stories: { ...(state?.stories || {}), [walletKey(w.address)]: storyKey(w) } };
}

export function watchFreshIso(watchlist) {
	if (!watchlist) return null;
	if (watchlist.computed_at) return watchlist.computed_at;
	const times = (watchlist.wallets || []).map((x) => x.checked_at).filter(Boolean).sort();
	return times.length ? times[times.length - 1] : null;
}

// --- storage -----------------------------------------------------------------

function isPlainObject(v) {
	return v !== null && typeof v === 'object' && !Array.isArray(v);
}

/** { state, ok }: ok=false means storage is unavailable, not that it was empty. */
export function readState(storage) {
	if (!storage) return { state: emptyState(), ok: false };
	let raw;
	try {
		raw = storage.getItem(STORAGE_KEY);
	} catch {
		return { state: emptyState(), ok: false };
	}
	if (raw == null) return { state: emptyState(), ok: true };
	try {
		const v = JSON.parse(raw);
		if (!isPlainObject(v)) return { state: emptyState(), ok: true };
		return {
			state: {
				reviewed: isPlainObject(v.reviewed) ? v.reviewed : {},
				stories: isPlainObject(v.stories) ? v.stories : {},
				streak: isPlainObject(v.streak) ? v.streak : null
			},
			ok: true
		};
	} catch {
		return { state: emptyState(), ok: true };
	}
}

export function writeState(storage, state) {
	if (!storage) return false;
	try {
		storage.setItem(STORAGE_KEY, JSON.stringify(state));
		return true;
	} catch {
		return false;
	}
}
```

- [ ] **Step 4: Run tests.** `npm test`, expecting all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/review.js src/lib/review.test.js
git commit -m "feat(review): review state, streak, stories and storage"
```

---

### Task 3: Toast store

**Files:**
- Create: `dashboard/src/lib/ui/toast.js`
- Create: `dashboard/src/lib/ui/toast.test.js`
- Modify: `dashboard/package.json`: test script becomes `node --test src/lib/*.test.js src/lib/ui/*.test.js`

**Interfaces:**
- Produces: `toasts` (a Svelte-store-contract object with `subscribe`), `toast(message, {action?: {label, run}, duration?: number}) → id`, `dismiss(id)`, `runAction(id)`, `MAX_TOASTS` = 3

- [ ] **Step 1: Failing tests**

```js
// src/lib/ui/toast.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { toasts, toast, dismiss, runAction, MAX_TOASTS } from './toast.js';

function current() {
	let v;
	const un = toasts.subscribe((x) => (v = x));
	un();
	return v;
}

test('toast queues, caps, dismisses', () => {
	for (const t of current()) dismiss(t.id);
	const ids = [];
	for (let i = 0; i < MAX_TOASTS + 2; i++) ids.push(toast(`m${i}`, { duration: 0 }));
	const list = current();
	assert.equal(list.length, MAX_TOASTS);
	assert.equal(list[list.length - 1].message, `m${MAX_TOASTS + 1}`, 'newest kept, oldest evicted');
	dismiss(ids[ids.length - 1]);
	assert.equal(current().length, MAX_TOASTS - 1);
	for (const t of current()) dismiss(t.id);
});

test('runAction runs the callback once and dismisses', () => {
	let n = 0;
	const id = toast('undoable', { duration: 0, action: { label: 'Undo', run: () => n++ } });
	runAction(id);
	runAction(id);
	assert.equal(n, 1);
	assert.equal(current().find((t) => t.id === id), undefined);
});

test('subscribers are notified', () => {
	const seen = [];
	const un = toasts.subscribe((v) => seen.push(v.length));
	const id = toast('x', { duration: 0 });
	dismiss(id);
	un();
	assert.deepEqual(seen.slice(-2), [seen[seen.length - 2], seen[seen.length - 2] - 1]);
});
```

- [ ] **Step 2: Update the test script in `package.json`** to `"test": "node --test src/lib/*.test.js src/lib/ui/*.test.js"`. Run `npm test`, expecting FAIL (toast.js missing).

- [ ] **Step 3: Implement `src/lib/ui/toast.js`**

```js
// src/lib/ui/toast.js
// Toast queue. Implements the Svelte store contract by hand (subscribe returns
// an unsubscribe) so it has no import and runs under node --test.

export const MAX_TOASTS = 3;
const DEFAULT_MS = 4000;

let list = [];
let nextId = 1;
const subs = new Set();
const timers = new Map();

function emit() {
	for (const fn of subs) fn(list);
}

export const toasts = {
	subscribe(fn) {
		subs.add(fn);
		fn(list);
		return () => subs.delete(fn);
	}
};

export function dismiss(id) {
	const t = timers.get(id);
	if (t) clearTimeout(t);
	timers.delete(id);
	const before = list.length;
	list = list.filter((x) => x.id !== id);
	if (list.length !== before) emit();
}

/** duration 0 = stays until dismissed. */
export function toast(message, { action = null, duration = DEFAULT_MS } = {}) {
	const id = nextId++;
	list = [...list, { id, message, action }];
	while (list.length > MAX_TOASTS) dismiss(list[0].id);
	emit();
	if (duration > 0) timers.set(id, setTimeout(() => dismiss(id), duration));
	return id;
}

export function runAction(id) {
	const t = list.find((x) => x.id === id);
	if (!t) return;
	dismiss(id);
	t.action?.run?.();
}
```

Note: `while (list.length > MAX_TOASTS) dismiss(list[0].id)` emits an intermediate state. That is acceptable, since the final `emit()` carries the settled list.

- [ ] **Step 4: Run tests.** `npm test`, expecting all PASS.

- [ ] **Step 5: Commit**

```bash
git add package.json src/lib/ui/toast.js src/lib/ui/toast.test.js
git commit -m "feat(ui): toast queue with undo action"
```

---

### Task 4: UI kit components and tokens

**Files:**
- Modify: `dashboard/src/app.css` (append tokens and reduced motion)
- Create: `dashboard/src/lib/ui/Avatar.svelte`, `Sheet.svelte`, `Segmented.svelte`, `ProgressRing.svelte`, `Pips.svelte`, `Skeleton.svelte`, `Toast.svelte`

**Interfaces:**
- Consumes: `avatar()` (Task 1), `toasts`, `dismiss`, `runAction` (Task 3)
- Produces:
  - `<Avatar address size ring>` where ring is `'none'|'seen'|'unseen'|'alert'`
  - `<Sheet open title on:close>` with a slot
  - `<Segmented options bind:value label>` where options are `[{value,label,count}]`
  - `<ProgressRing done total size>`
  - `<Pips count max>`
  - `<Skeleton width height radius>`
  - `<Toast/>` (the host)
  - The CSS vars `--radius-sm/md/lg`, `--dur-fast/--dur/--dur-slow`, `--ease-out`, `--shadow-sheet`

These are presentational, so they are verified by build (and rendered in Task 5) rather than unit tests. Each must meet the checklist states listed in ARCHITECTURE.md §13.

- [ ] **Step 1: Append to `src/app.css`**

```css
/* --- Tokens for the phone app's UI kit (dashboard/ARCHITECTURE.md §13) ---- */
:root {
	--radius-sm: 8px;
	--radius-md: 14px;
	--radius-lg: 22px;
	--dur-fast: 100ms;
	--dur: 180ms;
	--dur-slow: 240ms;
	--ease-out: cubic-bezier(0.23, 1, 0.32, 1);
	--shadow-sheet: 0 -8px 32px rgba(0, 0, 0, 0.45);
}
@media (prefers-reduced-motion: reduce) {
	:root { --dur-fast: 0ms; --dur: 0ms; --dur-slow: 0ms; }
	*, *::before, *::after { animation-duration: 0ms !important; transition-duration: 0ms !important; }
}
```

- [ ] **Step 2: `src/lib/ui/Avatar.svelte`**

```svelte
<script>
	// Gradient identicon: you learn "the purple one" before you read hex.
	import { avatar } from '$lib/review.js';

	export let address = '';
	export let size = 40;
	/** 'none' | 'seen' | 'unseen' | 'alert' */
	export let ring = 'none';

	$: av = avatar(address);
</script>

<span class="avatar ring-{ring}" style="--size:{size}px" role="img" aria-label="Wallet {address}">
	<span
		class="face"
		style="background: linear-gradient({av.angle}deg, hsl({av.a} 72% 58%), hsl({av.b} 68% 42%))"
	></span>
</span>

<style>
	.avatar {
		width: var(--size);
		height: var(--size);
		border-radius: 50%;
		display: inline-grid;
		place-items: center;
		flex: none;
	}
	.face { width: 100%; height: 100%; border-radius: 50%; }
	.ring-seen, .ring-unseen, .ring-alert { padding: 2.5px; }
	.ring-seen { background: var(--border); }
	.ring-unseen { background: conic-gradient(from 200deg, var(--accent-cyan), var(--accent-purple), var(--accent-cyan)); }
	.ring-alert { background: var(--accent-red); }
	.ring-seen .face, .ring-unseen .face, .ring-alert .face { border: 2.5px solid var(--bg-primary); }
</style>
```

- [ ] **Step 3: `src/lib/ui/Sheet.svelte`**

```svelte
<script>
	// Bottom sheet on native <dialog>: showModal() gives focus trapping, Esc and
	// an inert page for free, which is most of the checklist's Modal row.
	import { createEventDispatcher } from 'svelte';

	export let open = false;
	export let title = '';

	const dispatch = createEventDispatcher();
	const id = `sheet-${Math.random().toString(36).slice(2, 9)}`;
	let dialog;

	$: if (dialog) {
		if (open && !dialog.open) dialog.showModal();
		else if (!open && dialog.open) dialog.close();
	}

	function onClose() {
		open = false;
		dispatch('close');
	}
	// A click whose target is the <dialog> itself landed on the backdrop.
	function onClick(e) {
		if (e.target === dialog) dialog.close();
	}
</script>

<dialog bind:this={dialog} class="sheet" aria-labelledby={id} on:close={onClose} on:click={onClick}>
	<div class="panel">
		<div class="grabber" aria-hidden="true"></div>
		<header>
			<h2 {id}>{title}</h2>
			<button class="close" on:click={() => dialog.close()} aria-label="Close">✕</button>
		</header>
		<div class="body"><slot /></div>
	</div>
</dialog>

<style>
	dialog.sheet {
		margin: auto 0 0;
		width: 100%;
		max-width: 100%;
		max-height: 88dvh;
		padding: 0;
		border: 0;
		background: transparent;
		color: var(--text-primary);
	}
	dialog.sheet::backdrop { background: rgba(0, 0, 0, 0.55); }
	.panel {
		max-width: 560px;
		margin: 0 auto;
		max-height: 88dvh;
		overflow-y: auto;
		background: var(--bg-card);
		border-radius: var(--radius-lg) var(--radius-lg) 0 0;
		box-shadow: var(--shadow-sheet);
		padding: 8px 18px calc(20px + env(safe-area-inset-bottom, 0px));
	}
	dialog[open] .panel { animation: sheet-in var(--dur-slow) var(--ease-out); }
	@keyframes sheet-in { from { transform: translateY(100%); } to { transform: translateY(0); } }
	.grabber { width: 40px; height: 5px; border-radius: 3px; background: var(--border); margin: 0 auto 10px; }
	header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
	h2 { font-size: 1.05rem; margin: 0; }
	.close {
		min-width: 44px; min-height: 44px; border: 0; background: transparent;
		color: var(--text-secondary); font-size: 1.1rem; border-radius: 50%;
	}
	.body { padding-top: 8px; }
</style>
```

- [ ] **Step 4: `src/lib/ui/Segmented.svelte`**

```svelte
<script>
	// Segmented control. Switching is instant with no slide: tabs are the most
	// frequent action in the app, and motion there reads as lag.
	import { createEventDispatcher } from 'svelte';

	/** @type {{value: string, label: string, count?: number|null}[]} */
	export let options = [];
	export let value = '';
	export let label = '';

	const dispatch = createEventDispatcher();
	let buttons = [];

	function select(v) {
		value = v;
		dispatch('change', v);
	}
	function onKey(e, i) {
		if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
		e.preventDefault();
		const n = options.length;
		const j = (i + (e.key === 'ArrowRight' ? 1 : n - 1)) % n;
		select(options[j].value);
		buttons[j]?.focus();
	}
</script>

<div class="segmented" role="tablist" aria-label={label}>
	{#each options as o, i (o.value)}
		<button
			role="tab"
			bind:this={buttons[i]}
			aria-selected={value === o.value}
			tabindex={value === o.value ? 0 : -1}
			class:selected={value === o.value}
			on:click={() => select(o.value)}
			on:keydown={(e) => onKey(e, i)}
		>
			{o.label}{#if o.count != null}<span class="count">{o.count}</span>{/if}
		</button>
	{/each}
</div>

<style>
	.segmented {
		display: flex;
		gap: 4px;
		padding: 4px;
		background: var(--bg-secondary);
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
	}
	button {
		flex: 1;
		min-height: 44px;
		border: 0;
		border-radius: calc(var(--radius-md) - 4px);
		background: transparent;
		color: var(--text-secondary);
		font: inherit;
		font-weight: 600;
		font-size: 0.9rem;
	}
	button.selected { background: var(--bg-card-hover); color: var(--text-primary); }
	.count { margin-left: 6px; font-variant-numeric: tabular-nums; color: var(--text-muted); font-weight: 500; }
</style>
```

- [ ] **Step 5: `src/lib/ui/ProgressRing.svelte`, `Pips.svelte`, `Skeleton.svelte`**

```svelte
<!-- ProgressRing.svelte -->
<script>
	export let done = 0;
	export let total = 0;
	export let size = 56;
	const stroke = 6;
	$: r = (size - stroke) / 2;
	$: c = 2 * Math.PI * r;
	$: pct = total > 0 ? Math.min(1, done / total) : 1;
</script>

<svg
	width={size} height={size} viewBox="0 0 {size} {size}"
	role="progressbar" aria-valuemin="0" aria-valuemax={total} aria-valuenow={done}
	aria-label="{done} of {total} reviewed"
>
	<circle cx={size / 2} cy={size / 2} {r} fill="none" stroke="var(--border)" stroke-width={stroke} />
	<circle
		cx={size / 2} cy={size / 2} {r} fill="none"
		stroke="var(--accent-cyan)" stroke-width={stroke} stroke-linecap="round"
		stroke-dasharray={c} stroke-dashoffset={c * (1 - pct)}
		transform="rotate(-90 {size / 2} {size / 2})"
	/>
</svg>
```

```svelte
<!-- Pips.svelte -->
<script>
	// A count of independent vectors, NOT a score. Two agreeing vectors is the
	// project's strongest evidence, so the meter shows agreement and nothing else.
	export let count = 0;
	export let max = 3;
	$: n = Math.max(max, count);
</script>

<span class="pips" role="img" aria-label="{count} independent vector{count === 1 ? '' : 's'}">
	{#each Array(n) as _, i}<span class="pip" class:on={i < count}></span>{/each}
</span>

<style>
	.pips { display: inline-flex; gap: 4px; vertical-align: middle; }
	.pip { width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
	.pip.on { background: var(--accent-cyan); }
</style>
```

```svelte
<!-- Skeleton.svelte -->
<script>
	export let width = '100%';
	export let height = '16px';
	export let radius = 'var(--radius-sm)';
</script>

<span class="skeleton" style="width:{width};height:{height};border-radius:{radius}" aria-hidden="true"></span>

<style>
	.skeleton {
		display: block;
		background: linear-gradient(90deg, var(--bg-card) 0%, var(--bg-card-hover) 50%, var(--bg-card) 100%);
		background-size: 200% 100%;
		animation: shimmer 1.4s linear infinite;
	}
	@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
</style>
```

- [ ] **Step 6: `src/lib/ui/Toast.svelte`**

```svelte
<script>
	import { fly } from 'svelte/transition';
	import { cubicOut } from 'svelte/easing';
	import { toasts, dismiss, runAction } from './toast.js';

	const reduced = typeof matchMedia === 'function'
		&& matchMedia('(prefers-reduced-motion: reduce)').matches;
	const t = { y: 16, duration: reduced ? 0 : 180, easing: cubicOut };
</script>

<div class="host" role="status" aria-live="polite">
	{#each $toasts as item (item.id)}
		<div class="toast" transition:fly={t}>
			<span>{item.message}</span>
			{#if item.action}
				<button class="action" on:click={() => runAction(item.id)}>{item.action.label}</button>
			{:else}
				<button class="action muted" on:click={() => dismiss(item.id)} aria-label="Dismiss">✕</button>
			{/if}
		</div>
	{/each}
</div>

<style>
	.host {
		position: fixed;
		left: 0; right: 0;
		bottom: calc(16px + env(safe-area-inset-bottom, 0px));
		display: flex; flex-direction: column; align-items: center; gap: 8px;
		pointer-events: none;
		z-index: 50;
		padding: 0 16px;
	}
	.toast {
		pointer-events: auto;
		display: flex; align-items: center; gap: 12px;
		max-width: 520px; width: 100%;
		background: var(--text-primary); color: var(--bg-primary);
		border-radius: var(--radius-md);
		padding: 4px 4px 4px 16px;
		font-weight: 500;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
	}
	.toast span { flex: 1; }
	.action {
		min-height: 44px; min-width: 44px; padding: 0 14px;
		border: 0; border-radius: var(--radius-sm);
		background: transparent; color: var(--accent-blue); font: inherit; font-weight: 700;
	}
	.action.muted { color: var(--bg-card-hover); }
</style>
```

- [ ] **Step 7: Build.** `npm run build`, expecting success with no Svelte errors (warnings about unused CSS are acceptable only if they come from existing files).

- [ ] **Step 8: Commit**

```bash
git add src/app.css src/lib/ui/*.svelte
git commit -m "feat(ui): phone UI kit (avatar, sheet, segmented, progress, pips, skeleton, toast)"
```

---

### Task 5: The `/review` route

**Files:**
- Modify: `dashboard/src/routes/+layout.svelte` (bare shell on /review)
- Create: `dashboard/src/routes/review/+page.svelte`, `Stories.svelte`, `WatchDetail.svelte`, `ReviewProgress.svelte`, `WalletPost.svelte`

**Interfaces:**
- Consumes: everything in Tasks 1–4, plus `fetchRoster`, `fetchWatchlist`, `formatUSD`, `shortAddr`, `addressUrl` from `api.js`, and `Addr.svelte`
- Produces: the page. The component events are:
  - `WalletPost`: `on:reviewed` (detail: the row), `on:copy` (detail: the address), `on:why` (detail: the row)
  - `Stories`: `on:open` (detail: the watch wallet)
  - `ReviewProgress`: `on:markall`

- [ ] **Step 1: Layout.** In `src/routes/+layout.svelte`, add `$: bare = $page.url.pathname.startsWith(`${base}/review`);` in the script, and wrap the existing markup so `/review` gets only the slot:

```svelte
{#if bare}
	<slot />
{:else}
	<div class="app-shell"> …existing sidebar + main… </div>
	<nav class="mobile-nav"> …existing… </nav>
{/if}
```

The existing `<slot />` inside `main` stays. Svelte allows a slot in each branch because only one renders.

- [ ] **Step 2: `WalletPost.svelte`**

```svelte
<script>
	import { createEventDispatcher } from 'svelte';
	import { addressUrl, formatUSD, shortAddr } from '$lib/api.js';
	import { TIER_LABEL, vectorLabel, bornLabel } from '$lib/review.js';
	import Avatar from '$lib/ui/Avatar.svelte';
	import Pips from '$lib/ui/Pips.svelte';

	/** A feed row: a roster wallet plus `status`. */
	export let w;
	/** The tier it had when you last reviewed it, for "was X". */
	export let reviewedTier = null;

	const dispatch = createEventDispatcher();
	$: href = addressUrl(w.wallet);
	$: ev = w.evidence || {};
	$: totals = ev.totals || {};
	$: unread = w.status !== 'reviewed';
</script>

<article class="post" class:unread>
	<header>
		<Avatar address={w.wallet} size={40} />
		<div class="who">
			<span class="mono short">{shortAddr(w.wallet)}</span>
			<span class="meta">
				{#if w.known_self}his (config) · {/if}born {bornLabel(ev.hl_birth_ms)}
			</span>
		</div>
		<div class="tags">
			<span class="tier tier-{String(w.tier).toLowerCase()}">{TIER_LABEL[w.tier] || w.tier}</span>
			{#if w.status === 'new'}<span class="flag new">NEW</span>{/if}
			{#if w.status === 'changed'}<span class="flag changed">was {TIER_LABEL[reviewedTier] || reviewedTier}</span>{/if}
		</div>
	</header>

	<div class="evidence">
		<Pips count={w.vector_count || 0} />
		<span class="agree">
			{w.vector_count || 0} vector{w.vector_count === 1 ? '' : 's'} agree
			{#if w.tier_dropped_from}· peaked {TIER_LABEL[w.tier_dropped_from] || w.tier_dropped_from}{/if}
		</span>
	</div>
	{#if w.vectors?.length}
		<div class="chips">{#each w.vectors as v}<span class="chip">{vectorLabel(v)}</span>{/each}</div>
	{/if}

	<dl class="stats">
		<div><dt>HL value</dt><dd>{formatUSD(ev.hl_account_value)}</dd></div>
		<div><dt>From him</dt><dd>{formatUSD(totals.received_from_target_usd)}</dd></div>
		<div><dt>To him</dt><dd>{formatUSD(totals.sent_to_target_usd)}</dd></div>
		<div><dt>Chains</dt><dd>{ev.chains?.length ?? '—'}</dd></div>
	</dl>

	{#if href}
		<a class="address mono" {href} target="_blank" rel="noopener noreferrer"
		   on:click={() => dispatch('reviewed', w)}>{w.wallet}</a>
	{:else}
		<span class="address mono">{w.wallet}</span>
	{/if}

	<footer>
		{#if href}
			<a class="btn primary" {href} target="_blank" rel="noopener noreferrer"
			   on:click={() => dispatch('reviewed', w)}>↗ Hypurrscan</a>
		{/if}
		<button class="btn" on:click={() => dispatch('copy', w.wallet)}>Copy</button>
		<button class="btn" on:click={() => dispatch('why', w)}>Why?</button>
		<button class="btn" disabled={!unread} on:click={() => dispatch('reviewed', w)}>
			{unread ? '✓ Reviewed' : 'Reviewed'}
		</button>
	</footer>
</article>

<style>
	.post {
		position: relative;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: 14px 16px;
		display: flex; flex-direction: column; gap: 10px;
	}
	.post.unread::before {
		content: ''; position: absolute; left: -5px; top: 26px;
		width: 10px; height: 10px; border-radius: 50%; background: var(--accent-cyan);
	}
	header { display: flex; align-items: center; gap: 10px; }
	.who { display: flex; flex-direction: column; min-width: 0; flex: 1; }
	.short { font-weight: 600; }
	.meta { font-size: 0.78rem; color: var(--text-muted); }
	.tags { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
	.tier, .flag {
		font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
		padding: 3px 8px; border-radius: 999px; white-space: nowrap;
	}
	.tier-confirmed { background: rgba(255, 51, 85, 0.15); color: var(--accent-red); }
	.tier-probable { background: rgba(255, 170, 0, 0.15); color: var(--accent-yellow); }
	.tier-possible { background: rgba(0, 204, 221, 0.15); color: var(--accent-cyan); }
	.tier-watch { background: rgba(136, 136, 160, 0.15); color: var(--text-secondary); }
	.flag.new { background: var(--accent-cyan); color: var(--bg-primary); }
	.flag.changed { background: var(--accent-yellow); color: var(--bg-primary); }
	.evidence { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; color: var(--text-secondary); }
	.chips { display: flex; flex-wrap: wrap; gap: 6px; }
	.chip {
		font-size: 0.78rem; padding: 4px 10px; border-radius: 999px;
		background: var(--bg-secondary); border: 1px solid var(--border); color: var(--text-secondary);
	}
	.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0; }
	.stats div { min-width: 0; }
	dt { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }
	dd { margin: 2px 0 0; font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 0.85rem; }
	.address {
		display: block; padding: 10px 12px; min-height: 44px;
		background: var(--bg-secondary); border-radius: var(--radius-md);
		font-size: 0.78rem; line-height: 1.5; word-break: break-all; color: var(--accent-blue);
	}
	footer { display: grid; grid-template-columns: 1.4fr 1fr 1fr 1.4fr; gap: 6px; }
	.btn {
		min-height: 44px; display: grid; place-items: center; padding: 0 6px;
		border-radius: var(--radius-md); border: 1px solid var(--border);
		background: transparent; color: var(--text-primary); font: inherit; font-size: 0.85rem; font-weight: 600;
		text-decoration: none; transition: transform var(--dur-fast) var(--ease-out);
	}
	.btn:active { transform: scale(0.97); }
	.btn.primary { background: var(--accent-cyan); border-color: var(--accent-cyan); color: var(--bg-primary); }
	.btn:disabled { color: var(--text-muted); }
</style>
```

- [ ] **Step 3: `Stories.svelte` and `WatchDetail.svelte`**

```svelte
<!-- Stories.svelte -->
<script>
	import { createEventDispatcher } from 'svelte';
	import { shortAddr } from '$lib/api.js';
	import { storyRing, ratio } from '$lib/review.js';
	import Avatar from '$lib/ui/Avatar.svelte';

	/** data/watchlist/latest.json, or null when it could not be loaded. */
	export let watch = null;
	export let state;
	const dispatch = createEventDispatcher();
	$: wallets = watch?.wallets || [];
</script>

<section aria-label="Close watch">
	{#if !watch}
		<p class="note">Close watch could not be loaded.</p>
	{:else if !wallets.length}
		<p class="note">Nothing under close watch.</p>
	{:else}
		<div class="row">
			{#each wallets as w (w.address)}
				<button class="story" on:click={() => dispatch('open', w)}>
					<Avatar address={w.address} size={60} ring={storyRing(w, state)} />
					<span class="mono label">{shortAddr(w.address)}</span>
					<span class="sub">{w.read_ok ? ratio(w.size_ratio) : 'unread'}</span>
				</button>
			{/each}
		</div>
	{/if}
</section>

<style>
	.row { display: flex; gap: 14px; overflow-x: auto; padding: 4px 2px 8px; scrollbar-width: none; }
	.row::-webkit-scrollbar { display: none; }
	.story {
		display: flex; flex-direction: column; align-items: center; gap: 4px;
		background: none; border: 0; color: inherit; padding: 0; min-width: 72px;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.story:active { transform: scale(0.95); }
	.label { font-size: 0.72rem; color: var(--text-secondary); }
	.sub { font-size: 0.68rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }
	.note { color: var(--text-muted); font-size: 0.85rem; margin: 0; }
</style>
```

```svelte
<!-- WatchDetail.svelte -->
<script>
	import Addr from '$lib/Addr.svelte';
	import { formatUSD, formatTime } from '$lib/api.js';
	import { ratio, ago, SIZE_BAND } from '$lib/review.js';

	export let w;
	export let now = Date.now();
	$: big = typeof w.size_ratio === 'number' && w.size_ratio >= SIZE_BAND;
</script>

<div class="detail">
	<Addr address={w.address} full className="mono addr" />
	{#if !w.read_ok}
		<p class="bad">Could not read this wallet on the last run.</p>
		{#if w.errors?.length}<ul class="errors">{#each w.errors as e}<li>{e}</li>{/each}</ul>{/if}
	{:else}
		<dl>
			<div><dt>Value</dt><dd>{formatUSD(w.account_value)}</dd></div>
			<div><dt>vs target</dt><dd class:bad={big}>{ratio(w.size_ratio)}</dd></div>
			<div><dt>Last fill</dt><dd title={formatTime(w.last_fill_ms)}>{ago(w.last_fill_ms, now)}</dd></div>
			<div><dt>Source</dt><dd>{w.source || '—'}</dd></div>
		</dl>
		<h3>Agents ({w.agents?.length ?? 0})</h3>
		{#each w.agents || [] as a}<Addr address={a} full className="mono addr small" />{/each}
		<h3>Sub-accounts ({w.subaccounts?.length ?? 0})</h3>
		{#each w.subaccounts || [] as s}<Addr address={typeof s === 'string' ? s : s?.subAccountUser} full className="mono addr small" />{/each}
	{/if}
	{#if w.why}<h3>Why it is watched</h3><p class="why">{w.why}</p>{/if}
</div>

<style>
	.detail { display: flex; flex-direction: column; gap: 10px; }
	.detail :global(.addr) {
		display: block; word-break: break-all; padding: 10px 12px; min-height: 44px;
		background: var(--bg-secondary); border-radius: var(--radius-md); font-size: 0.8rem;
	}
	.detail :global(.addr.small) { min-height: 0; font-size: 0.72rem; }
	dl { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 0; }
	dt { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }
	dd { margin: 2px 0 0; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
	h3 { font-size: 0.8rem; margin: 6px 0 0; color: var(--text-secondary); }
	.why { font-size: 0.85rem; line-height: 1.5; color: var(--text-secondary); margin: 0; }
	.bad { color: var(--accent-red); }
	.errors { font-size: 0.78rem; color: var(--text-muted); margin: 0; padding-left: 18px; }
</style>
```

The sub-account entries in the watch file may be objects; verify their shape against `data/watchlist/latest.json` at implementation time and adjust the accessor.

- [ ] **Step 4: `ReviewProgress.svelte`**

```svelte
<script>
	import { createEventDispatcher } from 'svelte';
	import ProgressRing from '$lib/ui/ProgressRing.svelte';

	/** { done, total, fresh, changed } */
	export let p;
	const dispatch = createEventDispatcher();
	$: clear = p.total > 0 && p.done === p.total;
</script>

<section class="progress" aria-label="Review progress">
	{#if p.total === 0}
		<p class="title">Nothing to review</p>
	{:else if clear}
		<svg class="check" viewBox="0 0 52 52" width="44" height="44" aria-hidden="true">
			<circle cx="26" cy="26" r="24" fill="none" stroke="var(--accent-green)" stroke-width="3" />
			<path d="M15 27 l8 8 l15 -17" fill="none" stroke="var(--accent-green)" stroke-width="4"
			      stroke-linecap="round" stroke-linejoin="round" />
		</svg>
		<div>
			<p class="title">You're all caught up</p>
			<p class="sub">{p.total} wallet{p.total === 1 ? '' : 's'} reviewed</p>
		</div>
	{:else}
		<ProgressRing done={p.done} total={p.total} size={52} />
		<div class="text">
			<p class="title">{p.done} / {p.total} reviewed</p>
			<p class="sub">{p.fresh} new · {p.changed} changed</p>
		</div>
		<button class="all" on:click={() => dispatch('markall')}>Mark all ✓</button>
	{/if}
</section>

<style>
	.progress {
		display: flex; align-items: center; gap: 14px;
		background: var(--bg-card); border: 1px solid var(--border);
		border-radius: var(--radius-lg); padding: 14px 16px;
	}
	.text { flex: 1; }
	.title { margin: 0; font-weight: 700; font-variant-numeric: tabular-nums; }
	.sub { margin: 2px 0 0; font-size: 0.82rem; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
	.all {
		min-height: 44px; padding: 0 14px; border-radius: var(--radius-md);
		border: 1px solid var(--border); background: transparent; color: var(--text-primary);
		font: inherit; font-weight: 600; font-size: 0.85rem;
		transition: transform var(--dur-fast) var(--ease-out);
	}
	.all:active { transform: scale(0.97); }
	/* The one delight animation: rare by construction (the queue empties). */
	.check path { stroke-dasharray: 40; stroke-dashoffset: 40; animation: draw 400ms var(--ease-out) 120ms forwards; }
	@keyframes draw { to { stroke-dashoffset: 0; } }
</style>
```

- [ ] **Step 5: `review/+page.svelte`**

```svelte
<script>
	import { onMount } from 'svelte';
	import { fetchRoster, fetchWatchlist, shortAddr } from '$lib/api.js';
	import {
		feed, tabCounts, progress, dropped, dismissDropped, markReviewed, markStorySeen,
		checkIn, localDay, freshness, watchFreshIso, readState, writeState, emptyState,
		walletKey, vectorLabel, TIER_LABEL
	} from '$lib/review.js';
	import { toast } from '$lib/ui/toast.js';
	import Toast from '$lib/ui/Toast.svelte';
	import Sheet from '$lib/ui/Sheet.svelte';
	import Segmented from '$lib/ui/Segmented.svelte';
	import Skeleton from '$lib/ui/Skeleton.svelte';
	import Stories from './Stories.svelte';
	import WatchDetail from './WatchDetail.svelte';
	import ReviewProgress from './ReviewProgress.svelte';
	import WalletPost from './WalletPost.svelte';

	let roster = null;
	let watch = null;
	let loading = true;
	let failed = false;
	let offline = false;
	let now = Date.now();
	let state = emptyState();
	let storageOk = true;
	let tab = 'review';
	let sheet = null; // { kind: 'why' | 'watch' | 'dropped', w? }

	function storage() {
		try { return window.localStorage; } catch { return null; }
	}
	function persist() {
		storageOk = writeState(storage(), state);
	}

	async function load() {
		loading = true;
		const [r, wl] = await Promise.all([fetchRoster(), fetchWatchlist()]);
		now = Date.now();
		loading = false;
		if (!r) {
			// Rule 5: a failed read must not touch the review state.
			failed = true;
			offline = typeof navigator !== 'undefined' && navigator.onLine === false;
			return;
		}
		failed = false;
		offline = false;
		roster = r;
		watch = wl;
		const read = readState(storage());
		storageOk = read.ok;
		state = { ...read.state, streak: checkIn(read.state.streak, localDay(new Date())) };
		if (storageOk) persist();
	}

	onMount(load);

	function mark(rows, label) {
		if (!rows.length) return;
		const prev = state;
		state = markReviewed(state, rows);
		persist();
		toast(label, { action: { label: 'Undo', run: () => { state = prev; persist(); } } });
	}
	function copy(addr) {
		navigator.clipboard?.writeText(addr).then(
			() => toast('Address copied'),
			() => toast("Couldn't copy")
		);
	}
	function openStory(w) {
		state = markStorySeen(state, w);
		persist();
		sheet = { kind: 'watch', w };
	}
	function dismissDrop(wallet) {
		state = dismissDropped(state, wallet);
		persist();
	}

	$: rows = roster ? feed(roster, state, tab) : [];
	$: counts = roster ? tabCounts(roster, state) : null;
	$: prog = roster ? progress(feed(roster, state, 'review')) : null;
	$: drops = roster ? dropped(roster, state) : [];
	$: rosterFresh = freshness(roster?.computed_at, now);
	$: watchFresh = freshness(watchFreshIso(watch), now);
	$: streak = storageOk ? state.streak?.count || 0 : 0;
	$: tabs = [
		{ value: 'review', label: 'For review', count: counts?.review ?? null },
		{ value: 'watch', label: 'Watch', count: counts?.watch ?? null },
		{ value: 'all', label: 'All', count: counts?.all ?? null }
	];

	function fresh(f) {
		if (f.level === 'unknown') return 'age unknown';
		return f.minutes < 60 ? `${f.minutes}m ago` : `${Math.floor(f.minutes / 60)}h ago`;
	}
</script>

<svelte:head><title>Review · Ezekiel</title></svelte:head>

<div class="review">
	<header class="top">
		<div class="bar">
			<span class="brand">Ezekiel</span>
			<span class="spacer"></span>
			{#if streak > 0}
				<span class="streak" title="Days in a row you've checked in">🔥 {streak}</span>
			{/if}
			<button class="icon" on:click={load} aria-label="Refresh" disabled={loading}>⟳</button>
		</div>
		{#if roster}
			<p class="fresh">
				<span class="lvl-{rosterFresh.level}">Roster {fresh(rosterFresh)}</span>
				· <span class="lvl-{watchFresh.level}">Watch {watch ? fresh(watchFresh) : 'unavailable'}</span>
			</p>
		{/if}
	</header>

	{#if loading && !roster}
		<div class="stack">
			<Skeleton height="72px" radius="var(--radius-lg)" />
			<Skeleton height="72px" radius="var(--radius-lg)" />
			<Skeleton height="220px" radius="var(--radius-lg)" />
			<Skeleton height="220px" radius="var(--radius-lg)" />
		</div>
	{:else if failed && !roster}
		<div class="empty">
			<p class="title">{offline ? "You're offline" : "Couldn't load the roster"}</p>
			<p class="sub">Nothing is shown rather than an old list.</p>
			<button class="btn" on:click={load}>Retry</button>
		</div>
	{:else if roster}
		{#if failed}<p class="warn">Refresh failed. This is the list from {fresh(rosterFresh)}.</p>{/if}
		{#if !storageOk}<p class="warn">Can't remember your reviews on this device.</p>{/if}

		<Stories {watch} {state} on:open={(e) => openStory(e.detail)} />
		{#if prog}<ReviewProgress p={prog} on:markall={() => mark(feed(roster, state, 'review').filter((r) => r.status !== 'reviewed'), 'All marked reviewed')} />{/if}
		<Segmented options={tabs} bind:value={tab} label="Wallet list" />

		<div class="stack">
			{#each rows as w (w.wallet)}
				<WalletPost
					{w}
					reviewedTier={state.reviewed[walletKey(w.wallet)] ?? null}
					on:reviewed={(e) => mark([e.detail], 'Marked reviewed')}
					on:copy={(e) => copy(e.detail)}
					on:why={(e) => (sheet = { kind: 'why', w: e.detail })}
				/>
			{:else}
				<div class="empty"><p class="sub">No wallets in this tab.</p></div>
			{/each}
		</div>

		{#if drops.length}
			<button class="drops" on:click={() => (sheet = { kind: 'dropped' })}>
				{drops.length} wallet{drops.length === 1 ? '' : 's'} left the list ›
			</button>
		{/if}
	{/if}
</div>

<Sheet
	open={!!sheet}
	title={sheet?.kind === 'watch' ? 'Close watch' : sheet?.kind === 'dropped' ? 'Left the list' : 'Why this wallet'}
	on:close={() => (sheet = null)}
>
	{#if sheet?.kind === 'watch'}
		<WatchDetail w={sheet.w} {now} />
	{:else if sheet?.kind === 'why'}
		<p class="sub">{sheet.w.vectors.map(vectorLabel).join(' · ') || 'No vectors'}</p>
		<ul class="reasons">{#each sheet.w.reasons || [] as r}<li>{r}</li>{:else}<li>No reasons recorded.</li>{/each}</ul>
	{:else if sheet?.kind === 'dropped'}
		<ul class="reasons">
			{#each drops as d (d.wallet)}
				<li class="drop">
					<span class="mono">{shortAddr(d.wallet)}</span>
					<span class="sub">was {TIER_LABEL[d.tier] || d.tier}</span>
					<button class="btn small" on:click={() => dismissDrop(d.wallet)}>Dismiss</button>
				</li>
			{/each}
		</ul>
	{/if}
</Sheet>

<Toast />

<style>
	.review {
		max-width: 560px;
		margin: 0 auto;
		padding: env(safe-area-inset-top, 0px) 16px calc(96px + env(safe-area-inset-bottom, 0px));
		display: flex; flex-direction: column; gap: 14px;
	}
	.top {
		position: sticky; top: 0; z-index: 10;
		margin: 0 -16px; padding: 10px 16px 8px;
		padding-top: calc(10px + env(safe-area-inset-top, 0px));
		margin-top: calc(-1 * env(safe-area-inset-top, 0px));
		background: color-mix(in srgb, var(--bg-primary) 82%, transparent);
		backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
		border-bottom: 1px solid var(--border);
	}
	.bar { display: flex; align-items: center; gap: 10px; }
	.brand { font-weight: 800; font-size: 1.25rem; letter-spacing: -0.01em; }
	.spacer { flex: 1; }
	.streak { font-weight: 700; font-variant-numeric: tabular-nums; }
	.icon {
		min-width: 44px; min-height: 44px; border-radius: 50%;
		border: 1px solid var(--border); background: transparent; color: var(--text-primary); font-size: 1.1rem;
	}
	.fresh { margin: 4px 0 0; font-size: 0.78rem; color: var(--text-muted); }
	.lvl-ok { color: var(--text-secondary); }
	.lvl-warn, .lvl-unknown { color: var(--accent-yellow); }
	.lvl-stale { color: var(--accent-red); }
	.stack { display: flex; flex-direction: column; gap: 12px; }
	.empty { text-align: center; padding: 40px 12px; }
	.title { font-weight: 700; margin: 0; }
	.sub { color: var(--text-secondary); font-size: 0.85rem; }
	.warn { margin: 0; font-size: 0.82rem; color: var(--accent-yellow); }
	.btn {
		min-height: 44px; padding: 0 16px; border-radius: var(--radius-md);
		border: 1px solid var(--border); background: transparent; color: var(--text-primary); font: inherit; font-weight: 600;
	}
	.btn.small { min-height: 36px; padding: 0 12px; font-size: 0.8rem; }
	.drops {
		min-height: 48px; border-radius: var(--radius-lg); border: 1px dashed var(--border);
		background: transparent; color: var(--text-secondary); font: inherit;
	}
	.reasons { padding-left: 18px; line-height: 1.55; font-size: 0.9rem; }
	.drop { display: flex; align-items: center; gap: 10px; list-style: none; margin-left: -18px; padding: 6px 0; }
	.drop .sub { flex: 1; }
</style>
```

- [ ] **Step 6: Tests and build.** Run `npm test` (all PASS) and `npm run build` (success).

- [ ] **Step 7: Dev render.** Run `npm run dev` in the background, then `curl -s http://localhost:5173/review | head -5` and confirm HTML is served. Visual verification comes in Task 7.

- [ ] **Step 8: Commit**

```bash
git add src/routes/+layout.svelte src/routes/review
git commit -m "feat(review): phone review feed with stories, progress and sheets"
```

---

### Task 6: PWA shell

**Files:**
- Create: `dashboard/scripts/make_icons.mjs`, `dashboard/static/manifest.webmanifest`, `dashboard/static/icon-{180,192,512}.png` (generated), `dashboard/src/service-worker.js`, `dashboard/scripts/check_pwa_build.mjs`
- Modify: `dashboard/src/app.html`, `dashboard/package.json` (script `check:pwa`)

**Interfaces:**
- Produces: an installable build; `npm run check:pwa` exits non-zero if the build lacks the PWA files.

- [ ] **Step 1: `scripts/make_icons.mjs`**

```js
// Draws the app icons with no image library: a minimal PNG encoder on node:zlib.
// Run by hand: node scripts/make_icons.mjs   (outputs to static/)
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
	const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
	const td = Buffer.concat([Buffer.from(type), data]);
	const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td));
	return Buffer.concat([len, td, crc]);
}
function png(size, pixel) {
	const raw = Buffer.alloc(size * (size * 4 + 1));
	for (let y = 0; y < size; y++) {
		raw[y * (size * 4 + 1)] = 0;
		for (let x = 0; x < size; x++) {
			const [r, g, b] = pixel(x, y);
			const o = y * (size * 4 + 1) + 1 + x * 4;
			raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; raw[o + 3] = 255;
		}
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(size, 0); ihdr.writeUInt32BE(size, 4);
	ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
	return Buffer.concat([
		Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
		chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))
	]);
}

const BG = [10, 10, 15];
const CYAN = [0, 204, 221];
const PURPLE = [170, 102, 255];
const INK = [224, 224, 232];
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));

// Coverage of one sample point, in unit coordinates (0..1).
function sample(u, v) {
	const dx = u - 0.5, dy = v - 0.5;
	const r = Math.hypot(dx, dy);
	if (r > 0.34 && r < 0.40) {
		const t = (Math.atan2(dy, dx) / Math.PI + 1) / 2;
		return mix(CYAN, PURPLE, t < 0.5 ? t * 2 : (1 - t) * 2);
	}
	// "E": a spine plus three bars.
	const inE = (u > 0.39 && u < 0.46 && v > 0.36 && v < 0.64)
		|| (u > 0.39 && u < 0.62 && ((v > 0.36 && v < 0.42) || (v > 0.47 && v < 0.53) || (v > 0.58 && v < 0.64)));
	return inE ? INK : BG;
}

function draw(size) {
	const S = 4; // 4x4 supersampling
	return png(size, (x, y) => {
		const acc = [0, 0, 0];
		for (let i = 0; i < S; i++) for (let j = 0; j < S; j++) {
			const c = sample((x + (i + 0.5) / S) / size, (y + (j + 0.5) / S) / size);
			acc[0] += c[0]; acc[1] += c[1]; acc[2] += c[2];
		}
		return acc.map((v) => Math.round(v / (S * S)));
	});
}

for (const size of [180, 192, 512]) {
	writeFileSync(new URL(`../static/icon-${size}.png`, import.meta.url), draw(size));
	console.log(`static/icon-${size}.png`);
}
```

Run: `node scripts/make_icons.mjs`. Expected: three lines printed. Open `static/icon-512.png` with the Read tool to eyeball it.

- [ ] **Step 2: `static/manifest.webmanifest`**

```json
{
  "name": "Ezekiel Review",
  "short_name": "Ezekiel",
  "description": "Review the wallets Ezekiel has identified.",
  "start_url": "/Ezekiel/review",
  "scope": "/Ezekiel/",
  "display": "standalone",
  "background_color": "#0a0a0f",
  "theme_color": "#0a0a0f",
  "icons": [
    { "src": "/Ezekiel/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/Ezekiel/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
```

- [ ] **Step 3: `src/app.html`.** Replace the viewport meta and add the PWA tags in `<head>`:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<link rel="manifest" href="%sveltekit.assets%/manifest.webmanifest" />
<link rel="apple-touch-icon" href="%sveltekit.assets%/icon-180.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="Ezekiel" />
<meta name="theme-color" content="#0a0a0f" />
```

- [ ] **Step 4: `src/service-worker.js`**

```js
/// <reference types="@sveltejs/kit" />
// App-shell cache ONLY. Data (raw.githubusercontent.com) is never cached: a
// cached roster shown offline would present a stale list as the current one.
import { base, build, files, prerendered, version } from '$service-worker';

const CACHE = `ezekiel-shell-${version}`;
const SHELL = [...build, ...files, ...prerendered];

self.addEventListener('install', (event) => {
	event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
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
	if (url.origin !== self.location.origin) return; // data and Hypurrscan: untouched
	if (SHELL.includes(url.pathname)) {
		event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
		return;
	}
	if (req.mode === 'navigate') {
		// Network first so a deploy is picked up; the cached shell only when offline.
		event.respondWith(
			fetch(req).catch(async () =>
				(await caches.match(url.pathname)) || (await caches.match(`${base}/review`)) || Response.error()
			)
		);
	}
});
```

- [ ] **Step 5: `scripts/check_pwa_build.mjs`** plus the npm script `"check:pwa": "node scripts/check_pwa_build.mjs"`

```js
// Asserts the build is installable: run after `npm run build`.
import { existsSync, readFileSync } from 'node:fs';

const need = ['manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png', 'service-worker.js'];
let bad = 0;
for (const f of need) {
	if (!existsSync(`build/${f}`)) { console.error(`MISSING build/${f}`); bad++; }
}
const m = JSON.parse(readFileSync('build/manifest.webmanifest', 'utf8'));
if (!m.start_url.startsWith('/Ezekiel/')) { console.error(`start_url lacks base: ${m.start_url}`); bad++; }
const html = readFileSync('build/index.html', 'utf8');
for (const tag of ['rel="manifest"', 'apple-touch-icon', 'viewport-fit=cover']) {
	if (!html.includes(tag)) { console.error(`index.html lacks ${tag}`); bad++; }
}
if (bad) process.exit(1);
console.log('PWA build OK');
```

- [ ] **Step 6: Verify.** Run `npm run build && npm run check:pwa`. Expected: `PWA build OK`.

- [ ] **Step 7: Commit**

```bash
git add scripts/make_icons.mjs scripts/check_pwa_build.mjs static/manifest.webmanifest static/icon-*.png src/app.html src/service-worker.js package.json
git commit -m "feat(review): installable PWA shell (manifest, icons, app-shell service worker)"
```

---

### Task 7: Rendered verification and deploy

**Files:** none new. Fixes from verification go in the files they concern.

- [ ] **Step 1:** Start `npm run dev` (base `''`, so the page is `http://localhost:5173/review`; data comes live from `main`).
- [ ] **Step 2:** Headless Chrome at a phone size, as in memory `verifying_dashboard_2026_09_12`:
  `"C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --window-size=390,844 --virtual-time-budget=8000 --screenshot=<scratchpad>\review.png http://localhost:5173/review`
  Then Read the PNG. Check: header, stories, progress card, segmented control and posts all render; nothing overflows horizontally.
- [ ] **Step 3:** Dump the DOM (`--dump-dom`) and confirm that every `href` starting with `https://hypurrscan.io/address/0x` carries 40 hex characters, and that no `$0.00` renders where the JSON has null.
- [ ] **Step 4:** Run `npm test`, `npm run build` and `npm run check:pwa`, and run `python -m ruff check` only if Python changed (it should not).
- [ ] **Step 5:** Push to `main` **after the owner confirms**, since this triggers `deploy-dashboard.yml`. Watch the run with `gh run watch`.
- [ ] **Step 6:** On the phone: open `https://jayesh137.github.io/Ezekiel/review` in Safari, tap Share → Add to Home Screen, launch it, and confirm it is standalone and that a tapped address opens Hypurrscan.
