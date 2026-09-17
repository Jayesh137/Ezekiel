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
