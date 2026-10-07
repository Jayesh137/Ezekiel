// src/lib/study.test.js
// Run with: npm test (from dashboard/). Pure functions, no network, no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
	familyChips, studyRows, panelStatus, maxShare, histogramPath, habitRows, pct,
	runFaults, faultSummary, partialNote, unreadableMessage, isUnreadable,
	againstLine, statisticLine, byTestLine, sourceLabel, rhythmSeries, rhythmLabel
} from './study.js';

const A = '0x' + 'a'.repeat(40);
const B = '0x' + 'b'.repeat(40);
const C = '0x' + 'c'.repeat(40);
const D = '0x' + 'd'.repeat(40);

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

test('a chip tooltip says which test decided the verdict and on what basis', () => {
	const against = { families: { tooling: { verdict: 'against', by: ['T1:against'], basis: 'family' } } };
	assert.equal(familyChips(against, ['tooling'])[0].title,
		'Not his way of trading · decided by T1:against · basis family');
	const mixed = { families: { tooling: { verdict: 'mixed', by: ['T2', 'T1:against'], basis: 'family+self' } } };
	assert.equal(familyChips(mixed, ['tooling'])[0].title,
		'Mixed evidence · decided by T2, T1:against · basis family+self');
	// nothing decided it, so the tooltip is just the words
	const none = { families: { tooling: { verdict: 'uncalibrated', by: [], basis: null } } };
	assert.equal(familyChips(none, ['tooling'])[0].title, 'Not calibrated yet');
	assert.equal(familyChips({ families: { tooling: { verdict: 'neutral' } } }, ['tooling'])[0].title,
		'No signal either way');
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

test('studyRows: an unreadable archive sorts first, whatever its rank or size', () => {
	const fault = (wallet, value) => ({ wallet, rank: 0, account_value: value,
		families: { tooling: { verdict: 'unreadable' } } });
	const strong = { wallet: B, rank: 9, account_value: 99, families: { tooling: { verdict: 'for' } } };
	const weak = { wallet: C, rank: -3, account_value: 500, families: { tooling: { verdict: 'against' } } };
	assert.deepEqual(studyRows({ wallets: [strong, weak, fault(A, 1)] }).map((r) => r.wallet), [A, B, C]);
	// two faults: size, then address, exactly as for every other row
	assert.deepEqual(studyRows({ wallets: [strong, fault(A, 1), fault(D, 7)] }).map((r) => r.wallet), [D, A, B]);
	assert.equal(isUnreadable(fault(A, 1)), true);
	assert.deepEqual([isUnreadable(strong), isUnreadable({}), isUnreadable(null)], [false, false, false]);
});

test('runFaults reads the run\'s failures; a missing key is empty, never an error', () => {
	const study = {
		unreadable: [{ wallet: A, error: 'archive: corrupt day file' }],
		partial: [{ wallet: B, source: 'orders', error: 'HTTP 500' }],
		stopped: true
	};
	assert.deepEqual(runFaults(study), {
		unreadable: [{ wallet: A, error: 'archive: corrupt day file' }],
		partial: [{ wallet: B, source: 'orders', error: 'HTTP 500' }],
		stopped: true
	});
	const none = { unreadable: [], partial: [], stopped: false };
	assert.deepEqual(runFaults(null), none);
	assert.deepEqual(runFaults({}), none);
	assert.deepEqual(runFaults({ unreadable: 'oops', partial: {}, stopped: 0 }), none);
});

test('faultSummary says only what failed: unreadable, partial reads, then a stopped run', () => {
	assert.deepEqual(faultSummary(runFaults(null)), []);
	const faults = runFaults({
		unreadable: [{ wallet: A, error: 'archive: x' }],
		partial: [{ wallet: B, source: 'orders', error: 'e1' }, { wallet: B, source: 'ledger', error: 'e2' }],
		stopped: true
	});
	assert.deepEqual(faultSummary(faults), [
		'1 wallet unreadable',
		'2 partial reads (0xbbbb...bbbb: orders, 0xbbbb...bbbb: ledger)',
		'run stopped on its budget'
	]);
	assert.deepEqual(faultSummary(runFaults({ stopped: true })), ['run stopped on its budget']);
	assert.deepEqual(faultSummary(runFaults({ unreadable: [{ wallet: A }, { wallet: C }] })), ['2 wallets unreadable']);
	assert.deepEqual(faultSummary(runFaults({ partial: [{ wallet: C, source: 'ledger' }] })),
		['1 partial read (0xcccc...cccc: ledger)']);
});

test('partialNote names the sources a wallet only partly read, whatever the address case', () => {
	const faults = runFaults({ partial: [
		{ wallet: A, source: 'orders', error: 'e' },
		{ wallet: A, source: 'ledger', error: 'e' },
		{ wallet: A, source: 'orders', error: 'again' },
		{ wallet: B, source: 'orders', error: 'e' }] });
	assert.equal(partialNote(faults, A), 'partial read: orders, ledger');
	assert.equal(partialNote(faults, B.toUpperCase().replace('0X', '0x')), 'partial read: orders');
	assert.equal(partialNote(faults, C), null);
	assert.equal(partialNote(runFaults(null), A), null);
	// a partial read that names no source is still a partial read
	assert.equal(partialNote(runFaults({ partial: [{ wallet: C }] }), C), 'partial read');
});

test('an unreadable row says why, from the run\'s own record, never from an old dossier', () => {
	const faults = runFaults({ unreadable: [{ wallet: A, error: 'archive: corrupt day file' }] });
	assert.equal(unreadableMessage(faults, A),
		"This wallet's study archive could not be read: archive: corrupt day file");
	assert.equal(unreadableMessage(faults, C), "This wallet's study archive could not be read.");
	assert.equal(unreadableMessage(runFaults(null), A), "This wallet's study archive could not be read.");
});

test('againstLine explains an against verdict in his terms, and says nothing otherwise', () => {
	const against = { against: { status: 'against', traits: ['client_ids', 'maker'],
		same_op_mismatch: { client_ids: '0/41', maker: '0/41' }, lr: 0.1306 } };
	assert.equal(againstLine(against),
		'against: Client order IDs, Maker posting · same-operator mismatch 0/41 · LR 0.13');
	// pair counts that differ are shown per trait, never merged into one
	assert.equal(againstLine({ against: { status: 'against', traits: ['maker', 'triggers'],
		same_op_mismatch: { maker: '1/41', triggers: '0/40' }, lr: 0.0436 } }),
		'against: Maker posting, Trigger / TP / SL · same-operator mismatch Maker posting 1/41, Trigger / TP / SL 0/40 · LR 0.044');
	// uncalibrated, but the traits are still his-never-shown ones: show them, say it is not calibrated
	assert.equal(againstLine({ against: { status: 'uncalibrated', traits: ['client_ids'],
		strangers: 17, pairs: { client_ids: 12 } } }),
		'against, not calibrated yet: Client order IDs · same-operator pairs 12 · strangers 17');
	assert.equal(againstLine({ against: { status: 'uncalibrated', traits: ['client_ids'] } }),
		'against, not calibrated yet: Client order IDs');
	// a trait the table has no name for keeps the name it was written with
	assert.equal(againstLine({ against: { status: 'against', traits: ['spoofing'], lr: 0.5 } }),
		'against: spoofing · LR 0.50');
	for (const t1 of [null, undefined, {}, { against: null }, { against: { status: 'none' } },
		{ against: { status: 'neutral', traits: ['maker'] } },
		{ against: { status: 'uncalibrated' } }, { against: { status: 'uncalibrated', traits: [] } }]) {
		assert.equal(againstLine(t1), null);
	}
});

test('each test shows its raw number: a rhythm distance in seconds, a clip match strength', () => {
	assert.equal(statisticLine('T2', 0.1234), 'rhythm distance 0.12 s');
	assert.equal(statisticLine('T2', 0.0436), 'rhythm distance 0.044 s');
	assert.equal(statisticLine('T3', 0.85), 'clip match 0.85');
	// T1's statistic is a yes/no, carried by its verdict; a missing number is no line, never 0
	assert.equal(statisticLine('T1', true), null);
	assert.equal(statisticLine('T2', null), null);
	assert.equal(statisticLine('T3', NaN), null);
	assert.equal(statisticLine('T9', 1), null);
});

test('panelStatus reads each panel against its bar', () => {
	const study = { panels: { strangers: 210, family_pairs: 12, self_windows: 8,
		bars: { strangers: 200, family_pairs: 40, self_windows: 6 } } };
	assert.deepEqual(panelStatus(study).map((p) => p.ok), [true, false, true]);
	assert.deepEqual(panelStatus({}).map((p) => p.n), [null, null, null]);
});

test('panelStatus says whose counts the badges are and that either same-operator bar calibrates', () => {
	const study = { panels: { strangers: 17, measurable_strangers: 21, family_pairs: 12, self_windows: 8,
		bars: { strangers: 200, family_pairs: 40, self_windows: 6 } } };
	const [strangers, pairs, months] = panelStatus(study);
	assert.equal(strangers.name, 'Strangers T1 judges on (decided style)');
	assert.equal(strangers.title, 'Needs 200. Measurable strangers in all: 21.');
	const either = 'Either 40 family pairs or 6 of his own months calibrates a test.';
	assert.equal(pairs.title, `Needs 40. ${either}`);
	assert.equal(months.title, `Needs 6. ${either}`);
	// an older file without measurable_strangers or bars still has honest tooltips
	const old = panelStatus({ panels: { strangers: 3 } });
	assert.equal(old[0].title, 'Needs 200.');
	assert.equal(old[1].title, 'Needs 40. Either 40 family pairs or 6 of his own months calibrates a test.');
});

test('byTestLine lists T2 and T3 with their own counts, and nothing for an older file', () => {
	const study = { panels: { by_test: {
		T1: { strangers: 17, family_pairs: 12, self_windows: 8 },
		T2: { strangers: 248, family_pairs: 0, self_windows: 8 },
		T3: { strangers: 11, family_pairs: 1, self_windows: 5 } } } };
	assert.equal(byTestLine(study),
		'T2: 248 strangers · 0 pairs · 8 of his months; T3: 11 strangers · 1 pair · 5 of his months');
	assert.equal(byTestLine({ panels: { strangers: 3 } }), null);
	assert.equal(byTestLine({ panels: { by_test: { T1: { strangers: 1 } } } }), null);
	assert.equal(byTestLine(null), null);
	// a count the file lacks is a dash, never a zero
	assert.equal(byTestLine({ panels: { by_test: { T2: { strangers: null } } } }),
		'T2: — strangers · — pairs · — of his months');
});

test('sourceLabel names the four sources and leaves anything else as it was written', () => {
	assert.deepEqual(['pinned', 'roster_lead', 'decayed_lead', 'detector'].map(sourceLabel),
		['Pinned', 'Roster lead', 'Lead that decayed', 'Detector find']);
	assert.equal(sourceLabel('something_new'), 'something_new');
	assert.equal(sourceLabel('constructor'), 'constructor');
	assert.equal(sourceLabel(null), '—');
	assert.equal(sourceLabel(''), '—');
});

test('histograms share one scale and an empty one draws nothing', () => {
	const a = [0, 2, 2];
	const b = [0, 0, 4];
	assert.equal(maxShare(a, b), 1);
	assert.equal(histogramPath(a, 100, 50, 1), 'M0.0,50.0 L50.0,25.0 L100.0,25.0');
	assert.equal(histogramPath([0, 0], 100, 50, 1), '');
	assert.equal(histogramPath(null, 100, 50), '');
});

test('the rhythm chart states both gap counts, and which series has none', () => {
	const his = { cadence: [20000, 600, 49] };
	const it = { series: { cadence: [1000, 204] } };
	assert.deepEqual(rhythmSeries(his, it).map((s) => [s.key, s.label, s.gaps]),
		[['his', 'him', 20649], ['it', 'this wallet', 1204]]);
	assert.equal(rhythmLabel(his, it), 'Slicing rhythm: his 20,649 gaps, this wallet 1,204 gaps, 0 to 5 s');
	assert.equal(rhythmLabel(his, { series: { cadence: null } }),
		'Slicing rhythm: his 20,649 gaps, this wallet 0 gaps, 0 to 5 s');
	assert.equal(rhythmLabel({ cadence: [1] }, it), 'Slicing rhythm: his 1 gap, this wallet 1,204 gaps, 0 to 5 s');
	assert.equal(rhythmLabel(null, null), 'Slicing rhythm: his 0 gaps, this wallet 0 gaps, 0 to 5 s');
	// an all-zero histogram has no gaps either
	assert.deepEqual(rhythmSeries({ cadence: [0, 0] }, null).map((s) => s.gaps), [0, 0]);
});

test('habitRows keep a missing share as null and pct renders it as a dash', () => {
	const rows = habitRows({ series: { shares: { ioc: 0.5, taker: 1 } } }, { shares: { ioc: 0.988, taker: 1 } });
	const ioc = rows.find((r) => r.key === 'ioc');
	assert.deepEqual([ioc.his, ioc.it], [0.988, 0.5]);
	assert.equal(rows.find((r) => r.key === 'gtc').it, null);
	const taker = rows.find((r) => r.key === 'taker');
	assert.deepEqual([taker.label, taker.his, taker.it], ['Taker (crossed the book)', 1, 1]);
	assert.equal(pct(null), '—');
	assert.equal(pct(0.5), '50.0%');
	assert.equal(pct(0.0012), '0.12%');
});
