// src/lib/study.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	familyChips, studyRows, panelStatus, maxShare, histogramPath, habitRows, pct
} from './study.js';

const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);

test('familyChips follow a fixed order and never hide an unknown verdict', () => {
	const row = { families: { tooling: { verdict: 'against' }, timing: { verdict: 'weird' } } };
	const chips = familyChips(row, ['tooling', 'timing', 'lifecycle']);
	assert.deepEqual(chips.map((c) => [c.family, c.mark]), [['tooling', '✗'], ['timing', '?']]);
	assert.equal(chips[1].text, 'weird');
	assert.deepEqual(familyChips(null), []);
});

test('an unreadable archive is a fault that needs repair, never "no evidence"', () => {
	const chips = familyChips({ families: { tooling: { verdict: 'unreadable' } } }, ['tooling']);
	assert.equal(chips.length, 1);
	assert.equal(chips[0].mark, '!');
	assert.equal(chips[0].text, 'Archive unreadable — needs repair');
});

test('studyRows: rank first, then size; a missing rank sorts last, never as zero', () => {
	const study = { wallets: [
		{ wallet: A, rank: 0, account_value: 9 },
		{ wallet: B, rank: null, account_value: 99 },
		{ wallet: C, rank: 1.2, account_value: 1 }
	] };
	assert.deepEqual(studyRows(study).map((r) => r.wallet), [C, A, B]);
	assert.deepEqual(studyRows(null), []);
});

test('panelStatus reads each panel against its bar', () => {
	const study = { panels: { strangers: 210, family_pairs: 12, self_windows: 8,
		bars: { strangers: 200, family_pairs: 40, self_windows: 6 } } };
	assert.deepEqual(panelStatus(study).map((p) => p.ok), [true, false, true]);
	assert.deepEqual(panelStatus({}).map((p) => p.n), [null, null, null]);
});

test('histograms share one scale and an empty one draws nothing', () => {
	const a = [0, 2, 2];
	const b = [0, 0, 4];
	assert.equal(maxShare(a, b), 1);
	assert.equal(histogramPath(a, 100, 50, 1), 'M0.0,50.0 L50.0,25.0 L100.0,25.0');
	assert.equal(histogramPath([0, 0], 100, 50, 1), '');
	assert.equal(histogramPath(null, 100, 50), '');
});

test('habitRows keep a missing share as null and pct renders it as a dash', () => {
	const rows = habitRows({ series: { shares: { ioc: 0.5 } } }, { shares: { ioc: 0.988 } });
	const ioc = rows.find((r) => r.key === 'ioc');
	assert.deepEqual([ioc.his, ioc.it], [0.988, 0.5]);
	assert.equal(rows.find((r) => r.key === 'gtc').it, null);
	assert.equal(pct(null), '—');
	assert.equal(pct(0.5), '50.0%');
	assert.equal(pct(0.0012), '0.12%');
});
