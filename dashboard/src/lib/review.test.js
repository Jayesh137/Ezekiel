// src/lib/review.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
//
// review.js decides everything the phone review app shows: which wallets, in
// what order, what counts as new, what the streak is. A wrong branch here is
// a wallet the owner never sees, so every rule is pinned.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	eligible, feed, tabCounts, status, emptyState, vectorLabel, avatar,
	freshness, ago, ratio, bornLabel,
	markReviewed, dropped, dismissDropped, progress, localDay, checkIn,
	storyKey, storyRing, markStorySeen, watchFreshIso, readState, writeState, STORAGE_KEY,
	sessionFeed
} from './review.js';

const T = '0x45d26f28196d226497130c4bac709d808fed4029';
const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);
const D = '0x' + 'd'.repeat(40);
const upper = (addr) => '0x' + addr.slice(2).toUpperCase();

function w(wallet, tier, extra = {}) {
	return { wallet, tier, vectors: [], vector_count: 0, rank_strength: 0.1,
		is_service: false, reasons: [], evidence: {}, ...extra };
}

test('eligible drops infrastructure, services and the target', () => {
	const roster = { target: T, wallets: [
		w(A, 'POSSIBLE'), w(B, 'INFRASTRUCTURE'), w(C, 'WATCH', { is_service: true }),
		w(upper(T), 'CONFIRMED')
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
	assert.equal(status(w(upper(A), 'POSSIBLE'), s), 'reviewed');
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

test('an unknown tab falls back to review', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'WATCH')] };
	assert.deepEqual(feed(roster, emptyState(), 'nope').map((x) => x.wallet), [A]);
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

test('order: an absent strength is not treated as 0.0', () => {
	const roster = { target: T, wallets: [
		w(A, 'POSSIBLE', { rank_strength: undefined }),
		w(B, 'POSSIBLE', { rank_strength: 0 })
	] };
	assert.deepEqual(feed(roster, emptyState(), 'review').map((x) => x.wallet), [B, A]);
});

test('feed rows carry their status and do not mutate the roster', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE')] };
	assert.equal(feed(roster, emptyState(), 'review')[0].status, 'new');
	assert.equal(roster.wallets[0].status, undefined);
});

test('tabCounts', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'WATCH'), w(C, 'INFRASTRUCTURE')] };
	assert.deepEqual(tabCounts(roster, emptyState()), { review: 1, watch: 1, all: 2 });
});

test('vectorLabel: every roster vector in plain English, unknown raw', () => {
	for (const v of ['transfer', 'linkage', 'correlation', 'behavioural', 'hl_native',
		'shared_agent', 'explicit_link', 'dormancy_handoff', 'referral']) {
		assert.notEqual(vectorLabel(v), v, `${v} has a label`);
	}
	assert.equal(vectorLabel('something_new'), 'something_new');
});

test('avatar is deterministic and case-insensitive', () => {
	assert.deepEqual(avatar(A), avatar(upper(A)));
	assert.notDeepEqual(avatar(A), avatar(B));
	const { a, b, angle } = avatar(A);
	for (const v of [a, b, angle]) assert.ok(Number.isInteger(v) && v >= 0 && v < 360);
});

test('freshness thresholds', () => {
	const now = Date.parse('2026-09-22T12:00:00Z');
	const at = (m) => new Date(now - m * 60000).toISOString();
	assert.equal(freshness(at(10), now).level, 'ok');
	assert.equal(freshness(at(160), now).level, 'ok');
	assert.equal(freshness(at(161), now).level, 'warn');
	assert.equal(freshness(at(360), now).level, 'warn');
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

// --- review state ------------------------------------------------------------

test('markReviewed records the current tier and does not mutate', () => {
	const s0 = emptyState();
	const s1 = markReviewed(s0, [w(A, 'POSSIBLE'), w(upper(B), 'CONFIRMED')]);
	assert.deepEqual(s0.reviewed, {});
	assert.deepEqual(s1.reviewed, { [A]: 'POSSIBLE', [B]: 'CONFIRMED' });
});

test('dropped: reviewed wallets that left the eligible set', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'INFRASTRUCTURE')] };
	const s = { ...emptyState(), reviewed: { [A]: 'POSSIBLE', [B]: 'WATCH', [C]: 'POSSIBLE' } };
	assert.deepEqual(dropped(roster, s), [{ wallet: B, tier: 'WATCH' }, { wallet: C, tier: 'POSSIBLE' }]);
	assert.deepEqual(Object.keys(dismissDropped(s, upper(C)).reviewed).sort(), [A, B]);
	assert.deepEqual(Object.keys(s.reviewed).length, 3, 'dismiss does not mutate');
});

test('progress over feed rows', () => {
	const rows = [{ status: 'new' }, { status: 'changed' }, { status: 'reviewed' }, { status: 'reviewed' }];
	assert.deepEqual(progress(rows), { done: 2, total: 4, fresh: 1, changed: 1 });
	assert.deepEqual(progress([]), { done: 0, total: 0, fresh: 0, changed: 0 });
});

test('localDay uses the local calendar', () => {
	assert.equal(localDay(new Date(2026, 8, 2, 23, 59)), '2026-09-02');
	assert.equal(localDay(new Date(2026, 0, 1, 0, 0)), '2026-01-01');
});

test('checkIn: first, same day, next day, gap, month and year boundary, junk', () => {
	assert.deepEqual(checkIn(null, '2026-09-22'), { day: '2026-09-22', count: 1 });
	assert.deepEqual(checkIn({ day: '2026-09-22', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 4 });
	assert.deepEqual(checkIn({ day: '2026-09-21', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 5 });
	assert.deepEqual(checkIn({ day: '2026-09-19', count: 4 }, '2026-09-22'), { day: '2026-09-22', count: 1 });
	assert.deepEqual(checkIn({ day: '2026-09-30', count: 2 }, '2026-10-01'), { day: '2026-10-01', count: 3 });
	assert.deepEqual(checkIn({ day: '2026-12-31', count: 2 }, '2027-01-01'), { day: '2027-01-01', count: 3 });
	assert.deepEqual(checkIn({ day: '2028-02-28', count: 1 }, '2028-02-29'), { day: '2028-02-29', count: 2 });
	assert.deepEqual(checkIn({ day: 'junk', count: 9 }, '2026-09-22'), { day: '2026-09-22', count: 1 });
});

test('stories: alert beats unseen beats seen; last fill is not in the key', () => {
	const base = { address: A, read_ok: true, agents: ['x'], subaccounts: [], size_ratio: 0.7, last_fill_ms: 1 };
	assert.equal(storyKey(base), '1|0|0|1');
	assert.equal(storyRing(base, emptyState()), 'unseen');
	const seen = markStorySeen(emptyState(), base);
	assert.equal(storyRing(base, seen), 'seen');
	assert.equal(storyRing({ ...base, address: upper(A) }, seen), 'seen');
	assert.equal(storyRing({ ...base, last_fill_ms: 999 }, seen), 'seen');
	assert.equal(storyRing({ ...base, agents: ['x', 'y'] }, seen), 'unseen');
	assert.equal(storyRing({ ...base, size_ratio: 1.15 }, seen), 'alert');
	assert.equal(storyRing({ ...base, read_ok: false }, seen), 'alert');
	assert.equal(storyRing({ ...base, size_ratio: null }, seen), 'seen');
});

test('watchFreshIso: computed_at, else newest checked_at, else null', () => {
	assert.equal(watchFreshIso({ computed_at: 'X' }), 'X');
	assert.equal(watchFreshIso({ wallets: [{ checked_at: '2026-09-01T00:00:00Z' },
		{ checked_at: '2026-09-02T00:00:00Z' }] }), '2026-09-02T00:00:00Z');
	assert.equal(watchFreshIso({ wallets: [] }), null);
	assert.equal(watchFreshIso(null), null);
});

function fakeStorage(initial = {}) {
	const m = new Map(Object.entries(initial));
	return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

test('readState: absent, valid, corrupt, wrong shape, unavailable', () => {
	assert.deepEqual(readState(fakeStorage()), { state: emptyState(), ok: true });
	const good = { reviewed: { [A]: 'POSSIBLE' }, stories: {}, streak: { day: '2026-09-22', count: 2 } };
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: JSON.stringify(good) })), { state: good, ok: true });
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: '{nope' })), { state: emptyState(), ok: true });
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: '[1,2]' })).state, emptyState());
	assert.deepEqual(readState(fakeStorage({ [STORAGE_KEY]: '{"reviewed":[1]}' })).state, emptyState());
	assert.deepEqual(readState(null), { state: emptyState(), ok: false });
	assert.deepEqual(readState({ getItem() { throw new Error('denied'); } }), { state: emptyState(), ok: false });
});

test('writeState round-trips and reports failure', () => {
	const st = fakeStorage();
	const s = markReviewed(emptyState(), [w(A, 'POSSIBLE')]);
	assert.equal(writeState(st, s), true);
	assert.deepEqual(readState(st).state, s);
	assert.equal(writeState(null, s), false);
	assert.equal(writeState({ setItem() { throw new Error('quota'); } }, s), false);
});

test('sessionFeed: order and membership frozen at load, status live', () => {
	const roster = { target: T, wallets: [w(A, 'POSSIBLE'), w(B, 'WATCH'), w(C, 'POSSIBLE')] };
	// At load, B was reviewed as POSSIBLE and is now WATCH: changed, so in the tab.
	const atLoad = { ...emptyState(), reviewed: { [B]: 'POSSIBLE' } };
	const before = sessionFeed(roster, atLoad, atLoad, 'review').map((r) => r.wallet);
	// Reviewing A and B during the session must not move or drop them.
	const now = markReviewed(atLoad, [w(A, 'POSSIBLE'), w(B, 'WATCH')]);
	const after = sessionFeed(roster, atLoad, now, 'review');
	assert.deepEqual(after.map((r) => r.wallet), before);
	assert.deepEqual(after.map((r) => r.status), before.map((x) => (x === C ? 'new' : 'reviewed')));
});
