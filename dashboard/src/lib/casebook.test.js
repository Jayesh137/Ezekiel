// src/lib/casebook.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	pct, bandText, money, caseRows, familyChips, hlLine, calibrationLines, waterfall, timeline,
	lifeChart, rankedFeed, isNew, readSeen, writeSeen, SEEN_KEY
} from './casebook.js';

const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);
const K = '0x' + 'd'.repeat(40);

const INDEX = {
	cases: [
		{ address: K, rank: null, known: 'config:known_self', hl: { on_hl: true } },
		{ address: A, rank: 1, p: 0.03, p_now: 0.001, p_ceiling: 0.4,
			hl: { on_hl: true, value: 5e6, month_volume: 1e6, probed_at: 'x' },
			families: { infrastructure: [0.7, 1.5, 2.0, 'current'], behaviour: [-1, -0.5, -0.2, 'current'] },
			last_change: '2026-10-08T01:00:00Z' },
		{ address: B, rank: 2, p: 0.002, hl: { on_hl: false, probed_at: 'x' }, families: {},
			last_change: '2026-10-01T00:00:00Z' },
		{ address: C, rank: null, excluded: 'service: busy', hl: { on_hl: true } }
	]
};

test('pct reads like a person would say it, and never invents a number', () => {
	assert.deepEqual([0.999, 0.34, 0.034, 0.0034, 0.00001].map(pct), ['>99%', '34%', '3.4%', '0.34%', '<0.1%']);
	assert.equal(pct(null), '—');
	assert.equal(pct(NaN), '—');
	assert.equal(bandText({ p_now: 0.001, p_ceiling: 0.4 }), '0.10%–40%');
});

test('money is compact and absent is a dash', () => {
	assert.deepEqual([5.2e9, 52061448, 12345, 7].map(money), ['$5.2B', '$52.1M', '$12K', '$7']);
	assert.equal(money(undefined), '—');
});

test('caseRows keeps index order and filters as asked', () => {
	assert.deepEqual(caseRows(INDEX).map((r) => r.address), [K, A]);
	assert.deepEqual(caseRows(INDEX, { hlOnly: false }).map((r) => r.address), [K, A, B]);
	assert.deepEqual(caseRows(INDEX, { includeKnown: false, includeExcluded: true, hlOnly: false })
		.map((r) => r.address), [A, B, C]);
	assert.deepEqual(caseRows(INDEX, { query: '0xaaa' }).map((r) => r.address), [A]);
	assert.deepEqual(caseRows(null), []);
});

test('family chips are strongest first, and evidence against points down', () => {
	const chips = familyChips(INDEX.cases[1]);
	assert.deepEqual(chips.map((c) => [c.family, c.dir]), [['infrastructure', 'up'], ['behaviour', 'down']]);
	assert.equal(chips[0].label, 'Shared private infrastructure');
	assert.deepEqual(familyChips({}), []);
});

test('hlLine says what Hyperliquid knows, or that nobody asked yet', () => {
	assert.equal(hlLine(INDEX.cases[1]), '$5.0M on Hyperliquid · $1.0M traded in 30d');
	assert.equal(hlLine(INDEX.cases[2]), 'No Hyperliquid account');
	assert.equal(hlLine({ hl: {} }), 'Not read on Hyperliquid yet');
});

test('calibration lines name the recall check and the coherence sum', () => {
	const lines = calibrationLines({ calibration: {
		recall: [{ address: K, rank_among_unknown: 3 }], unknown_cases: 400, sum_p_central_unknown: 1.234 } });
	assert.match(lines[0], /ranks 3 among 400/);
	assert.match(lines[1], /1\.23/);
	assert.deepEqual(calibrationLines({}), []);
});

test('the waterfall runs from the prior through each family to the posterior', () => {
	const doc = { score: { central: -1.0, families: { money: { central: 1.0 }, infrastructure: { central: 1.5 } } } };
	const steps = waterfall(doc, -3);
	assert.deepEqual(steps.map((s) => s.total), [-3, -1.5, -0.5, -1.0]);
	assert.match(steps[3].label, /discount/);
	assert.deepEqual(waterfall({}, -3).map((s) => s.total), [-3]);
});

test('timeline orders evidence by first sight and keeps its status', () => {
	const doc = { evidence: {
		b: { kind: 'x', status: 'lapsed', first_seen: '2026-10-02T00:00:00Z', summary: 'two' },
		a: { kind: 'y', status: 'current', first_seen: '2026-09-10T00:00:00Z', summary: 'one' } } };
	assert.deepEqual(timeline(doc).map((t) => [t.key, t.status, t.first]),
		[['a', 'current', '2026-09-10'], ['b', 'lapsed', '2026-10-02']]);
});

test('the life chart scales, shades his silences and marks birth', () => {
	const DAY = 86400000;
	const doc = { hl: { birth_ms: 10 * DAY, life: [[10 * DAY, 0], [20 * DAY, 100], [30 * DAY, 50]] } };
	const chart = lifeChart(doc, [{ start_day: 15, end_day: 18 }, { start_day: 40, end_day: 45 }], 300, 100);
	assert.equal(chart.path, 'M0.0,100.0 L150.0,0.0 L300.0,50.0');
	assert.equal(chart.silences.length, 1);
	assert.equal(chart.birthX, 0);
	assert.equal(lifeChart({ hl: { life: [[1, 2]] } }), null);
});

test('the Ranked tab lists unknown Hyperliquid suspects only', () => {
	assert.deepEqual(rankedFeed(INDEX).map((r) => r.address), [A]);
	assert.deepEqual(rankedFeed({ cases: [{ address: A, rank: 1, hl: { on_hl: true }, ruling: 'not_him' }] }), []);
	assert.deepEqual(rankedFeed(undefined), []);
});

test('a row is new when it changed after the tab was last seen', () => {
	assert.equal(isNew(INDEX.cases[1], '2026-10-05T00:00:00Z'), true);
	assert.equal(isNew(INDEX.cases[2], '2026-10-05T00:00:00Z'), false);
	assert.equal(isNew(INDEX.cases[2], null), true);
});

test('seen-time storage survives a storage that throws', () => {
	const broken = { getItem() { throw new Error('private'); }, setItem() { throw new Error('private'); } };
	assert.equal(readSeen(broken), null);
	assert.equal(writeSeen(broken, 'x'), false);
	const mem = new Map();
	const ok = { getItem: (k) => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, v) };
	assert.equal(writeSeen(ok, '2026-10-08T00:00:00Z'), true);
	assert.equal(readSeen(ok), '2026-10-08T00:00:00Z');
	assert.equal(mem.has(SEEN_KEY), true);
});
