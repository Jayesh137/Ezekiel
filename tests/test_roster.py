# tests/test_roster.py
"""The roster tiers on how many INDEPENDENT vectors agree, not on one score.

Five detectors write five files. Read separately, a wallet showing up weakly in
three looks weaker than one showing up strongly in one — backwards, because the
ways the vectors can be fooled do not overlap.

The hazards pinned here are the ones that would let a single detector cast what
looks like two votes, and the one that let a stale record promote a wallet the
current scorer rejects.
"""

import json

import src.roster as roster

TARGET = "0x" + "11" * 20
W = "0x" + "22" * 20


def _write(tmp_path, name, key, items):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps({key: items}))


def _setup(tmp_path, monkeypatch, *, passed=True, **files):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    profile = tmp_path.parent / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "backtest.json").write_text(json.dumps({"passed": passed}))
    for name, (key, items) in files.items():
        _write(tmp_path, name, key, items)


def _node(**over):
    node = {"wallet": W, "classification": "DIRECT_RECIPIENT", "confidence": 0.3,
            "confidence_reasons": [], "evidence": {"transfer_count": 2},
            "totals": {}, "depth": 1, "chains": ["arbitrum"]}
    node.update(over)
    return node


def test_two_independent_vectors_and_confidence_reach_confirmed():
    assert roster.assign_tier({"transfer", "linkage"}, 0.7, False, False) \
        == roster.TIER_CONFIRMED


def test_one_vector_alone_cannot_reach_confirmed():
    """Every single vector has a known way of being wrong by itself."""
    assert roster.assign_tier({"correlation"}, 0.99, False, False) \
        == roster.TIER_PROBABLE


def test_high_confidence_without_two_vectors_is_only_probable():
    assert roster.assign_tier(set(), 0.9, False, False) == roster.TIER_PROBABLE


def test_a_service_is_never_a_candidate():
    assert roster.assign_tier({"transfer", "linkage"}, 0.99, True, False) \
        == roster.TIER_INFRASTRUCTURE


def test_operator_ground_truth_outranks_measurement():
    assert roster.assign_tier(set(), 0.0, False, True) == roster.TIER_CONFIRMED


def test_nothing_at_all_is_a_watch():
    assert roster.assign_tier(set(), 0.1, False, False) == roster.TIER_WATCH


def test_a_correlation_only_wallet_gets_no_transfer_vector(tmp_path, monkeypatch):
    """An inferred correlation edge is not an observed transfer. If it counted
    as one, the correlator alone would supply two 'independent' vectors and
    promote a wallet on a single amount coincidence."""
    _setup(tmp_path, monkeypatch,
           transfer_graph=("nodes", [_node(evidence={"transfer_count": 0},
                                           classification="CORRELATION_LEAD")]),
           correlations=("matches", [{"wallet": W, "confidence": 0.99}]))
    row = roster.build_roster({"target_wallet": TARGET})["wallets"][0]
    assert row["vectors"] == ["correlation"]
    # One vector at moderate confidence: a lead, not a conclusion.
    assert row["tier"] == roster.TIER_POSSIBLE


def test_transfer_plus_linkage_confirms(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           transfer_graph=("nodes", [_node(
               confidence=0.88,
               evidence={"transfer_count": 376, "shared_deposit_address": True})]))
    row = roster.build_roster({"target_wallet": TARGET})["wallets"][0]
    assert set(row["vectors"]) == {"transfer", "linkage"}
    assert row["tier"] == roster.TIER_CONFIRMED


def test_a_style_veto_cancels_the_behavioural_vote(tmp_path, monkeypatch):
    """A veto is a positive finding that this is a DIFFERENT human. It must not
    also count as a vote for being the same one."""
    _setup(tmp_path, monkeypatch, passed=True,
           candidates=("candidates", [{"wallet": W, "latest_score": 0.9,
                                       "latest_evidence": {"vetoes": ["freq 11x"]}}]))
    row = roster.build_roster({"target_wallet": TARGET})["wallets"][0]
    assert "behavioural" not in row["vectors"]


def test_an_unvalidated_scorer_casts_no_behavioural_vote(tmp_path, monkeypatch):
    """The load-bearing guard. The self-match backtest fails live, so the scorer
    cannot pick the target out of a lineup — it cannot be evidence that some
    other wallet is him. A stored record carrying vetoes:[] and the retired tier
    "CONFIRMED_CANDIDATE" promoted a wallet that scores 0.45 with a veto when
    re-scored."""
    _setup(tmp_path, monkeypatch, passed=False,
           candidates=("candidates", [{"wallet": W, "latest_score": 0.9,
                                       "latest_evidence": {"vetoes": []}}]))
    out = roster.build_roster({"target_wallet": TARGET})
    assert out["behavioural_counts_as_a_vector"] is False
    row = out["wallets"][0]
    assert "behavioural" not in row["vectors"]
    # Still recorded as context, just not as a vote.
    assert row["evidence"]["behavioural_score"] == 0.9


def test_a_validated_scorer_does_vote(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, passed=True,
           candidates=("candidates", [{"wallet": W, "latest_score": 0.9,
                                       "latest_evidence": {"vetoes": []}}]))
    out = roster.build_roster({"target_wallet": TARGET})
    assert out["behavioural_counts_as_a_vector"] is True
    assert "behavioural" in out["wallets"][0]["vectors"]


def test_the_target_is_never_in_his_own_roster(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch,
           transfer_graph=("nodes", [_node(wallet=TARGET)]))
    assert roster.build_roster({"target_wallet": TARGET})["wallets"] == []


def test_a_missing_detector_does_not_break_the_roster(tmp_path, monkeypatch):
    """Four vectors still say something when the fifth never ran."""
    _setup(tmp_path, monkeypatch,
           transfer_graph=("nodes", [_node()]))
    out = roster.build_roster({"target_wallet": TARGET})
    assert out["wallet_count"] == 1


def test_an_unreadable_detector_file_is_survived(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, transfer_graph=("nodes", [_node()]))
    (tmp_path / "correlations").mkdir(parents=True, exist_ok=True)
    (tmp_path / "correlations" / "latest.json").write_text("{not json")
    assert roster.build_roster({"target_wallet": TARGET})["wallet_count"] == 1


def test_rows_are_ordered_by_tier_then_vector_count(tmp_path, monkeypatch):
    other = "0x" + "33" * 20
    _setup(tmp_path, monkeypatch,
           transfer_graph=("nodes", [
               _node(wallet=other, confidence=0.2),
               _node(confidence=0.88,
                     evidence={"transfer_count": 5, "shared_deposit_address": True}),
           ]))
    rows = roster.build_roster({"target_wallet": TARGET})["wallets"]
    assert rows[0]["wallet"] == W
    assert rows[0]["tier"] == roster.TIER_CONFIRMED


def test_known_self_wallets_are_confirmed_from_config(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, transfer_graph=("nodes", [_node(confidence=0.0)]))
    out = roster.build_roster({"target_wallet": TARGET, "known_self_wallets": [W]})
    assert out["wallets"][0]["tier"] == roster.TIER_CONFIRMED
