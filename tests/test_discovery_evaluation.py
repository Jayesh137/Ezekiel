from src.discovery_evaluation import evaluate_replay

A, B, C = ['0x' + char * 40 for char in '123']


def trade(wallet, ts, tid=1):
    return {'kind': 'trade', 'observed_at_ms': ts, 'data':
            {'users': [wallet, C], 'coin': 'BTC', 'time': ts, 'tid': tid, 'side': 'B', 'px': '100', 'sz': '.01'}}


def test_small_non_leaderboard_wallet_reaches_real_registry_and_ranking():
    events = [trade(B, 1000), {'kind': 'fills', 'wallet': B, 'observed_at_ms': 2000, 'data': [
        {'coin': 'BTC', 'time': 1500, 'sz': '.01', 'px': '100', 'side': 'B', 'startPosition': '0', 'tid': 1}]}]
    report = evaluate_replay(events, [{'id': 'small', 'kind': 'synthetic', 'target_wallet': A,
                                      'expected_wallets': [B], 'start_ms': 500}], cutoff_ms=3000)
    row = report['scenarios'][0]
    assert row['stages']['observed'] == row['stages']['selected'] == row['stages']['ranked'] == [B]
    assert row['recall_at_5'] == 1
    assert report['production_alerts_sent'] == 0
    assert report['promotion_validated'] is False


def test_future_enrichment_and_unobserved_wallets_are_attributed_separately():
    report = evaluate_replay([trade(B, 1000), {'kind': 'fills', 'wallet': B, 'observed_at_ms': 5000, 'data': []}],
        [{'id': 'gap', 'kind': 'synthetic', 'target_wallet': A, 'expected_wallets': [B, C]}], cutoff_ms=2000)
    row = report['scenarios'][0]
    assert not row['stages']['enriched']
    assert row['misses'][B] == 'enrichment'
    assert report['future_events_excluded'] == 1


def test_delayed_relay_is_discovered_when_later_route_arrives():
    route = {'id': 'r1', 'recipient': B, 'original_funder': A, 'assertion': 'protocol_route', 'parent_event_ids': ['burn', 'mint']}
    report = evaluate_replay([{'kind': 'route', 'observed_at_ms': 5000, 'data': route}],
        [{'id': 'relay', 'kind': 'synthetic', 'target_wallet': A, 'expected_wallets': [B], 'start_ms': 1000}], cutoff_ms=6000)
    row = report['scenarios'][0]
    assert row['stages']['ranked'] == [B]
    assert row['first_investigation_latency_ms'][B] == 4000


def test_public_claim_is_not_accepted_as_verified_ground_truth():
    report = evaluate_replay([], [{'id': 'claim', 'kind': 'public_claim', 'target_wallet': A, 'expected_wallets': [B]}])
    assert report['scenarios'][0]['ground_truth_eligible'] is False


def test_selection_budget_and_retention_gaps_are_distinct_pipeline_failures():
    scenarios = [{'id': 'budget', 'kind': 'synthetic', 'target_wallet': A, 'expected_wallets': [B], 'scan_budget': 0}]
    assert evaluate_replay([trade(B, 1000)], scenarios)['scenarios'][0]['misses'][B] == 'selection'
    scenarios[0]['scan_budget'] = 20
    events = [trade(B, 1000), {'kind': 'coverage_gap', 'observed_at_ms': 1500, 'reason': 'fill_retention'}]
    row = evaluate_replay(events, scenarios)['scenarios'][0]
    assert row['misses'][B] == 'enrichment'
    assert row['coverage_gaps'][0]['reason'] == 'fill_retention'


def test_hide_known_seed_and_split_successor_routes_do_not_seed_identity_labels():
    events = [{'kind': 'route', 'observed_at_ms': 1000, 'data': {'id': w, 'recipient': w,
               'original_funder': A, 'assertion': 'observed_transfer', 'parent_event_ids': ['send:' + w]}}
              for w in [B, C]]
    row = evaluate_replay(events, [{'id': 'hidden-split', 'kind': 'synthetic', 'target_wallet': A,
                                   'expected_wallets': [B, C]}])['scenarios'][0]
    assert row['recall_at_5'] == 1
    assert not row['ground_truth_eligible']


def test_future_fill_inside_early_response_cannot_supply_enrichment():
    events = [trade(B, 1000), {'kind': 'fills', 'wallet': B, 'observed_at_ms': 2000,
                              'data': [{'coin': 'BTC', 'time': 9000, 'side': 'B', 'px': '100', 'sz': '1'}]}]
    row = evaluate_replay(events, [{'id': 'future-fill', 'kind': 'synthetic', 'target_wallet': A,
                                   'expected_wallets': [B]}])['scenarios'][0]
    assert row['misses'][B] == 'enrichment'


def test_noisy_controls_attribute_low_retrieval_to_ranking():
    wanted = '0x' + 'f' * 40
    events = []
    for i, wallet in enumerate([f'0x{n:040x}' for n in range(10, 35)] + [wanted]):
        ts = 1000 + i * 10
        events.extend([trade(wallet, ts, i), {'kind': 'fills', 'wallet': wallet, 'observed_at_ms': ts + 1,
            'data': [{'coin': 'BTC', 'time': ts, 'sz': '1', 'px': '100', 'side': 'B', 'startPosition': '0', 'tid': i}]}])
    row = evaluate_replay(events, [{'id': 'noisy', 'kind': 'synthetic', 'target_wallet': A,
                                   'expected_wallets': [wanted]}])['scenarios'][0]
    assert row['misses'][wanted] == 'ranking'
    assert row['recall_at_20'] == 0
    assert row['false_identity_alerts'] == 0
