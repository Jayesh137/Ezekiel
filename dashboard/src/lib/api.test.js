// src/lib/api.test.js
// Run with: node --test src/lib/  (from dashboard/). No test dependency:
// api.js is a plain ES module with no SvelteKit aliases, and node has a test
// runner built in. Pure functions only — nothing here touches the network.
//
// The dashboard had no tests at all, and it is the ONLY surface that reports
// whether alerting works: email cannot report its own failure, so a dead
// channel looks exactly like a quiet week everywhere else. A branch in
// getAlertDelivery that reads the health file wrongly is therefore a silent
// failure of the monitor of last resort.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { getAlertDelivery } from './api.js';

test('nothing to report when there is no health file', () => {
	assert.equal(getAlertDelivery(null), null);
});

test('a healthy channel reports how many alerts were withheld on purpose', () => {
	// The steady state since INFO was gated to the dashboard only: alerting
	// works, and the operator is deliberately not being buzzed for low-grade
	// discoveries. Both halves have to be visible, or the policy hides how
	// much it is withholding.
	const d = getAlertDelivery({
		healthy: true,
		suppressed: 7,
		last_success_at: '2026-09-11T17:23:59.575296+00:00'
	});

	assert.equal(d.down, false);
	assert.equal(d.withheld, 7);
});

test('a dead channel reports withheld alongside undelivered', () => {
	// Two different numbers that must never be read as each other: one is a
	// fault, the other is policy.
	const d = getAlertDelivery({
		healthy: false,
		undelivered: 2,
		suppressed: 7,
		last_failure_at: '2026-09-11T21:52:46+00:00',
		last_failure_reason: 'SMTPDataError: (502, ...)'
	});

	assert.equal(d.down, true);
	assert.equal(d.undelivered, 2);
	assert.equal(d.withheld, 7);
});

test('no suppressions reports zero, not undefined', () => {
	// The component compares `withheld > 0` to decide whether to show
	// anything. undefined > 0 is false too, but only by accident.
	const d = getAlertDelivery({ healthy: true, suppressed: 0 });
	assert.equal(d.withheld, 0);
});

test('a health file written before the split claims nothing', () => {
	// `suppressed` did not exist until 2026-09-11, and the deployed dashboard
	// reads whatever is on main. An absent counter means we do not know, so
	// the pill stays hidden — it must never render a wrong number or NaN.
	const d = getAlertDelivery({ healthy: true });
	assert.equal(d.withheld, 0);
});

import { feedFreshness, DETECTOR_FEEDS } from './api.js';

test('a feed inside its limit is fresh, past it stale', () => {
	const now = Date.parse('2026-09-17T03:00:00Z');
	assert.deepEqual(feedFreshness({ computed_at: '2026-09-17T02:30:00+00:00' }, 'computed_at', 60, now),
		{ status: 'fresh', ageMin: 30 });
	assert.deepEqual(feedFreshness({ computed_at: '2026-09-17T01:00:00+00:00' }, 'computed_at', 60, now),
		{ status: 'stale', ageMin: 120 });
});

test('a missing or undated feed is never fresh', () => {
	assert.equal(feedFreshness(null, 'computed_at', 60).status, 'missing');
	assert.equal(feedFreshness({ other: 1 }, 'computed_at', 60).status, 'missing');
	assert.equal(feedFreshness({ computed_at: 'not a date' }, 'computed_at', 60).status, 'missing');
});

test('every detector feed names a data path, a key and a limit above cadence', () => {
	for (const f of DETECTOR_FEEDS) {
		assert.ok(f.path.startsWith('data/') && f.path.endsWith('latest.json'), f.name);
		assert.ok(f.key && f.limitMin >= 360, f.name);
	}
});

import { blindSince, feedStatus } from './api.js';

test('a fresh feed whose reads are failing is blind, not healthy', () => {
	const now = Date.parse('2026-09-17T09:00:00Z');
	const feed = { name: 'Shared agents', key: 'computed_at', limitMin: 720 };
	const doc = { computed_at: '2026-09-17T08:30:00+00:00' };
	const blind = blindSince({ blind_since: { 'shared agents': '2026-09-17T02:00:00+00:00' } }, null);
	assert.deepEqual(feedStatus(doc, feed, blind, now), { status: 'blind', ageMin: 30, blindMin: 420 });
	assert.equal(feedStatus(doc, feed, {}, now).status, 'fresh');
	// Staleness outranks blindness: a feed that stopped writing is reported as stopped.
	assert.equal(feedStatus({ computed_at: '2026-09-16T00:00:00+00:00' }, feed, blind, now).status, 'stale');
});

test('every detector the watch or trace job checks for blindness has a dashboard row', () => {
	const names = new Set(DETECTOR_FEEDS.map((f) => f.name.toLowerCase()));
	for (const n of ['close watch', 'circle flows', 'roster', 'hl account surface', 'identities',
		'shared agents', 'dormancy handoff', 'behavioural scan', 'amount correlation']) {
		assert.ok(names.has(n), n);
	}
});

import { traceFindings, perimeterRows, provenanceRows, DETECTOR_FEEDS as FEEDS2 } from './api.js';

test('trace findings merge both files newest first and keep severity', () => {
	const boundary = { findings: [
		{ kind: 'outside_account_paid_his_world', severity: 'CRITICAL', hl_account: '0xa', counterparty: '0xb', role: 'deposit', amount_usd: 5, ts: 10, source: 'bridge2' },
		{ kind: 'perimeter_member_active_on_hl', severity: null, hl_account: '0xc', counterparty: '0xc', role: 'associate', ts: 30 }] };
	const provenance = { findings: [
		{ kind: 'provenance_touches_his_world', severity: 'HIGH', account: '0xd', member: '0xe', role: 'sink', usd: 7, ts: 20, hop: 2, route: 'bridge2' }] };
	const rows = traceFindings(boundary, provenance);
	assert.deepEqual(rows.map((r) => r.account), ['0xc', '0xd', '0xa']);
	assert.equal(rows[1].other, '0xe');
	assert.equal(rows[2].severity, 'CRITICAL');
	assert.ok(rows.every((r) => typeof r.label === 'string' && r.label.length));
	assert.deepEqual(traceFindings(null, undefined), []);
});

test('perimeter rows are ordered by role strength', () => {
	const rows = perimeterRows({ members: {
		'0x3': { address: '0x3', role: 'associate', weight: 0.3, why: 'two-way' },
		'0x1': { address: '0x1', role: 'core', weight: 1, why: 'config', hl: { active: true, checked_at: 'x' } },
		'0x2': { address: '0x2', role: 'deposit', weight: 1, why: 'binance' } } });
	assert.deepEqual(rows.map((r) => r.role), ['core', 'deposit', 'associate']);
	assert.equal(rows[0].active, true);
	assert.deepEqual(perimeterRows(null), []);
});

test('provenance rows carry a readable verdict', () => {
	const rows = provenanceRows({ recent: [{ account: '0x9', verdict: 'same_exchange', routes: ['bridge2'], usd: 1, hop1: [], resolved_at: 1 }] });
	assert.equal(rows[0].verdictLabel, 'Funded by his exchange');
	assert.deepEqual(provenanceRows({}), []);
});

test('the trace feeds are on the detector list', () => {
	const names = new Set(FEEDS2.map((f) => f.name.toLowerCase()));
	for (const n of ['boundary attribution', 'his perimeter', 'funding provenance']) assert.ok(names.has(n), n);
});
