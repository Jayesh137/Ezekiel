from src.successor_hypotheses import find_successor_hypotheses

A = "0x" + "1" * 40
B = "0x" + "2" * 40
C = "0x" + "3" * 40


def state(ts, qty):
    return {"ts_ms": ts, "positions": {"BTC": qty}}


def test_exposure_handoff_is_research_and_common_market_event_is_reported():
    target = {"wallet": A, "fills": [], "positions_history": [state(1000, 10), state(2000, 0)]}
    candidate = {"wallet": B, "fills": [], "positions_history": [state(1000, 0), state(2200, 10)]}
    result = find_successor_hypotheses(target, [candidate], {"market_events": [{"coin": "BTC", "ts_ms": 2000}]})[0]
    assert result["position_handoffs"]
    assert "common_market_event" in result["confounders"]
    assert result["promotable"] is False


def test_private_return_route_stays_visible_with_different_style():
    result = find_successor_hypotheses({"wallet": A, "fills": []}, [{"wallet": B, "fills": []}],
        {"routes": [{"id": "return", "original_funder": B, "recipient": A,
                      "assertion": "observed_transfer", "parent_event_ids": ["return"]}]} )[0]
    assert result["return_routes"]
    assert "return" in result["parent_event_ids"]
    assert result["episode_similarity"]["status"] == "insufficient_data"


def test_unlinked_group_cannot_be_combined_into_a_match():
    result = find_successor_hypotheses({"wallet": A, "fills": []},
        [{"wallet": B, "fills": []}, {"wallet": C, "fills": []}],
        {"linked_groups": [{"wallets": [B, C], "relationship": "similar_style"}]})
    assert all(not row.get("group_members") for row in result)


def test_manual_public_disclosure_is_a_dated_hypothesis_not_ground_truth():
    result = find_successor_hypotheses({"wallet": A, "fills": []}, [{"wallet": B, "fills": []}],
        {"as_of_ms": 100, "disclosures": [{"wallet": B, "observed_at_ms": 10,
          "url": "https://example.org/public-post", "claim": "unverified alias"}]} )[0]
    assert result["disclosures"]
    assert result["identity_confirmed"] is False
    assert result["copier_cohort"]["status"] == "insufficient_data"


def test_future_states_and_disclosures_cannot_enter_replay():
    result = find_successor_hypotheses(
        {"wallet": A, "fills": [], "positions_history": [state(1000, 10), state(2000, 0)]},
        [{"wallet": B, "fills": [], "positions_history": [state(1000, 0), state(2200, 10)]}],
        {"as_of_ms": 1500, "disclosures": [{"wallet": B, "observed_at_ms": 2000, "url": "https://example.org"}]})[0]
    assert not result['position_handoffs']
    assert not result['disclosures']


def test_group_book_requires_observed_relationship_and_synchronised_states():
    target = {"wallet": A, "fills": [], "positions_history": [state(1000, 10), state(2000, 0)]}
    candidates = [{"wallet": w, "fills": [], "positions_history": [state(1000, 0), state(2200, q)]}
                  for w, q in [(B, 4), (C, 6)]]
    result = find_successor_hypotheses(target, candidates, {"linked_groups": [
        {"wallets": [B, C], "relationship": "subaccount", "parent_event_ids": ["authority:1"]}]})
    group = next(row for row in result if row.get('group_members'))
    assert group['position_handoffs'][0]['quantity_ratio'] == 1
    assert 'authority:1' in group['parent_event_ids']
    assert not group['identity_confirmed']


def test_service_return_is_not_private_corroboration():
    result = find_successor_hypotheses({"wallet": A, "fills": []}, [{"wallet": B, "fills": []}],
        {"services": [A], "routes": [{"id": "router", "original_funder": B, "recipient": A,
         "assertion": "observed_transfer", "parent_event_ids": ["tx"]}]})[0]
    assert not result['return_routes']


def test_fill_position_transitions_support_handoffs_without_inventing_snapshots():
    common = {'coin': 'BTC', 'px': '100', 'sz': '10'}
    target = {'wallet': A, 'fills': [{**common, 'tid': 1, 'time': 1000, 'startPosition': '10', 'side': 'A'}]}
    candidate = {'wallet': B, 'fills': [{**common, 'tid': 2, 'time': 1100, 'startPosition': '0', 'side': 'B'}]}
    result = find_successor_hypotheses(target, [candidate])[0]
    assert result['position_handoffs'][0]['quantity_ratio'] == 1
    assert 'market_control_missing' in result['confounders']


def test_execution_slices_are_one_quantity_handoff():
    common = {'coin': 'BTC', 'px': '100'}
    target = {'wallet': A, 'fills': [{**common, 'tid': i, 'time': 1000 + i, 'sz': '1',
                                     'startPosition': str(10 - i), 'side': 'A'} for i in range(10)]}
    candidate = {'wallet': B, 'fills': [{**common, 'tid': 50, 'time': 1100, 'sz': '10', 'startPosition': '0', 'side': 'B'}]}
    handoffs = find_successor_hypotheses(target, [candidate])[0]['position_handoffs']
    assert len(handoffs) == 1 and handoffs[0]['quantity_ratio'] == 1


def test_offline_report_keeps_sparse_funding_lead_and_reports_missing_cache(tmp_path):
    from scripts.check_successor_hypotheses import run
    from src.candidate_registry import observe_candidate
    from src.utils import atomic_write_json
    observe_candidate(B, {'source': 'funding_route', 'positive': True}, tmp_path)
    atomic_write_json(tmp_path / 'routes' / 'latest.json', {'routes': [
        {'id': 'return', 'original_funder': B, 'recipient': A, 'assertion': 'observed_transfer',
         'parent_event_ids': ['return']}], 'authority': {}})
    report = run({'target_wallet': A}, data_dir=tmp_path, as_of_ms=1000)
    assert report['investigations'][0]['return_routes']
    assert report['coverage']['candidates_with_fills'] == 0
    assert report['coverage']['cached_fills_status'] == 'missing'
    assert (tmp_path / 'investigations' / 'latest.json').exists()
def test_numeric_string_snapshots_and_fill_references_are_normalised():
    from src.episodes import build_episodes
    from src.successor_hypotheses import _changes, _states
    account = {'wallet': '0x' + '1' * 40, 'positions_history': [
        {'ts_ms': '1000', 'positions': {'BTC': 1}}]}
    assert _states(account, 2000)[0]['ts_ms'] == 1000
    fill = {'wallet': account['wallet'], 'time': 1000, 'tid': 1, 'coin': 'BTC',
            'sz': '1', 'px': '10', 'side': 'B', 'startPosition': '0'}
    account = {'wallet': account['wallet'], 'fills': [fill, {**fill, 'time': '1000'}]}
    changes = _changes(account, 2000, True)
    episodes = build_episodes(account['fills'])
    assert changes[0]['quantity'] == 1
    assert changes[0]['parent_event_ids'] == episodes[0]['parent_event_ids']


def test_busy_funding_queue_cannot_starve_unfunded_public_discoveries(tmp_path):
    from scripts.check_successor_hypotheses import run
    from src.candidate_registry import observe_candidate
    factual = ['0x' + f'{i:040x}' for i in range(10, 15)]
    for wallet in factual:
        observe_candidate(wallet, {'source': 'funding_route', 'positive': True}, tmp_path)
    observe_candidate(B, {'source': 'public_trades', 'positive': True}, tmp_path)
    first = run({'target_wallet': A}, data_dir=tmp_path, as_of_ms=1000, limit=5)
    wallets = {r['wallet'] for r in first['investigations']}
    assert B in wallets and len(wallets & set(factual)) == 4
    missed = set(factual) - wallets
    second = run({'target_wallet': A}, data_dir=tmp_path, as_of_ms=2000, limit=5)
    assert missed <= {r['wallet'] for r in second['investigations']}
