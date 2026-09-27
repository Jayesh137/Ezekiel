import test from 'node:test';
import assert from 'node:assert/strict';
import { discoveryView, rosterTierLabel } from './discovery.js';

test('missing discovery reports do not imply zero findings or successful collection', () => {
  const view = discoveryView({}, 1_000_000);
  assert.equal(view.walletsObserved, null);
  assert.equal(view.collection.label, 'Not collected');
  assert.equal(view.evaluationLabel, 'Not evaluated');
  assert.deepEqual(view.leads, []);
});

test('a recent failed attempt retains the age of the last successful read', () => {
  const now = 90_000_000;
  const view = discoveryView({discovery: {computed_at_ms: now,
    last_successful_read_ms: now - 13 * 3600_000, last_run: {status: 'error'}},
    state: {status: 'restore_error', pending_shards: 2}}, now);
  assert.equal(view.collection.level, 'warn');
  assert.match(view.collection.label, /failed/i);
  assert.equal(view.lastSuccessAgeHours, 13);
  assert.ok(view.warnings.some(w => /restore/i.test(w)));
});

test('priority remains a research rank, with a concrete next check and no invented ownership', () => {
  const view = discoveryView({investigations: {computed_at_ms: 1000, investigations: [
    {wallet: '0x' + '1'.repeat(40), priority: 8, independent_sessions: 2,
      funding_routes: [{id: 'r', parent_event_ids: ['tx']}],
      next_measurement: 'confirm destination credit', confounders: ['market_control_missing']}
  ]}, quality: {evaluation_status: 'reported_research'}}, 2000);
  assert.equal(view.leads[0].nextCheck, 'confirm destination credit');
  assert.deepEqual(view.leads[0].reasons, ['1 funding route', '2 observed sessions']);
  assert.equal(view.evaluationLabel, 'Research replay only');
  assert.equal(view.leads[0].identityConfirmed, false);
});

test('legacy inferred confirmation is not presented as operator ground truth', () => {
  assert.equal(rosterTierLabel({tier: 'CONFIRMED', known_self: false}), 'Legacy inference');
  assert.equal(rosterTierLabel({tier: 'CONFIRMED', known_self: true}), 'Trusted seed');
  assert.equal(rosterTierLabel({tier: 'PROBABLE'}), 'Priority lead');
});

test('partial scanner enrichment stays visible alongside successful market collection', () => {
  const view = discoveryView({quality: {scanner_coverage: {status: 'partial',
    stopped_reason: 'rate_limited', phases: {priority: {deferred: 80}, leaderboard: {deferred: 450}}}}});
  assert.ok(view.warnings.some(w => /530/.test(w) && /rate limit/i.test(w)));
});
