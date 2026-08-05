# tests/test_threshold_coherence.py
"""Every module that judges behavioural similarity must judge it against the
SAME thresholds the scanner alerts on.

Four modules used to compare similarity against a literal 0.65 while the
thresholds actually in force were 0.5963 / 0.6463 / 0.6963, adapted down to the
measured self-match ceiling of 0.7163. 0.65 matched no tier boundary at all, so:

  * the headline risk score could award a perfect re-identification only 19% of
    the weight its own design allocated to it;
  * a WATCH_CLOSELY candidate contributed nothing to transfer-graph grading;
  * the tracer alerted on a wallet the style vetoes had rejected, using a score
    it had reached weeks earlier.

These tests pin the coherence rather than the numbers: they derive expectations
from thresholds.resolve() so a future re-validation moves them together.
"""

import json

import pytest

from src import risk, tracer
from src import thresholds as th
from src import transfer_graph as tg
from src.utils import candidate_current_score

W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def eff():
    """A resolved threshold set with a validated ceiling, as in production."""
    return th.resolve(
        {"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65},
        {"passed": True, "self_score": 0.7163, "run_at": "2026-07-29T01:45:19+00:00",
         "scoring_schema": th.SCORING_SCHEMA},
    )


# --- the shared primitives -------------------------------------------------------

def test_behavioural_gate_is_the_resolved_medium_threshold(eff):
    """'Trades like the target' must mean the tier the system labels WATCH_CLOSELY,
    not a literal that no longer corresponds to any boundary."""
    assert th.behavioural_gate(eff) == eff["medium"]
    assert th.behavioural_gate(eff) != 0.65


def test_behavioural_strength_is_full_at_the_self_match_ceiling(eff):
    """The ceiling is what the REAL trader scores against his own history. A
    wallet matching that well is the strongest evidence the scorer can produce,
    so it must carry full weight."""
    assert th.behavioural_strength(eff["self_match_ceiling"], eff) == pytest.approx(1.0)
    assert th.behavioural_strength(0.99, eff) == pytest.approx(1.0)


def test_behavioural_strength_is_zero_at_or_below_the_gate(eff):
    assert th.behavioural_strength(eff["medium"], eff) == pytest.approx(0.0)
    assert th.behavioural_strength(0.10, eff) == pytest.approx(0.0)


def test_behavioural_strength_rises_monotonically_across_the_band(eff):
    lo = th.behavioural_strength(eff["medium"] + 0.001, eff)
    mid = th.behavioural_strength(eff["high"], eff)
    hi = th.behavioural_strength(eff["self_match_ceiling"], eff)
    assert 0.0 < lo < mid < hi


def test_behavioural_strength_falls_back_to_high_without_a_validated_ceiling():
    """With no proven ceiling the high threshold is the only defensible anchor;
    the function must still be well-defined rather than dividing by zero."""
    unvalidated = th.resolve(
        {"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65}, None)
    assert unvalidated["self_match_ceiling"] is None
    assert th.behavioural_strength(unvalidated["high"], unvalidated) == pytest.approx(1.0)
    assert th.behavioural_strength(unvalidated["medium"], unvalidated) == pytest.approx(0.0)


# --- current vs all-time score ---------------------------------------------------

def test_candidate_current_score_prefers_latest_over_all_time_best():
    """best_score is the high-water mark. Asking 'does this wallet match now?'
    with it is what let a wallet scoring 0.13 keep firing CRITICAL alerts."""
    assert candidate_current_score(
        {"best_score": 0.7113, "latest_score": 0.1322}) == pytest.approx(0.1322)


def test_candidate_current_score_falls_back_to_best_when_latest_absent():
    assert candidate_current_score({"best_score": 0.70}) == pytest.approx(0.70)
    assert candidate_current_score({}) == pytest.approx(0.0)


# --- risk score calibration ------------------------------------------------------

def test_confirmed_tier_candidate_contributes_most_of_the_top_candidate_weight(eff):
    """A candidate at the CONFIRMED threshold is the strongest behavioural
    evidence this system produces. Anchoring the scale at 1.0 gave it 2.9 of 22
    points; the weight exists to be reachable."""
    r = risk.compute_risk_score({"top_candidate_score": eff["high"]}, eff)
    pts = next(f["points"] for f in r["factors"] if f["signal"] == "top_candidate")
    assert pts >= 0.6 * risk.WEIGHTS["top_candidate"]


def test_self_match_ceiling_candidate_earns_full_top_candidate_weight(eff):
    r = risk.compute_risk_score({"top_candidate_score": eff["self_match_ceiling"]}, eff)
    pts = next(f["points"] for f in r["factors"] if f["signal"] == "top_candidate")
    assert pts == pytest.approx(risk.WEIGHTS["top_candidate"], abs=0.05)


def test_candidate_below_the_gate_contributes_nothing(eff):
    r = risk.compute_risk_score({"top_candidate_score": eff["medium"] - 0.01}, eff)
    assert all(f["signal"] != "top_candidate" for f in r["factors"])


def test_risk_signals_use_the_candidates_current_score(tmp_path, monkeypatch):
    """risk answers 'is he migrating right now', so a candidate that peaked weeks
    ago must not be reported at its peak."""
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(risk, "read_cursor", lambda n: 0)
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [
        {"wallet": W1, "best_score": 0.7505, "latest_score": 0.6117},
        {"wallet": W2, "best_score": 0.7019, "latest_score": 0.7019},
    ]}))

    signals = risk._gather_signals()
    assert signals["top_candidate_score"] == pytest.approx(0.7019)
    assert signals["top_candidate_wallet"] == W2


# --- the combined-alert route ----------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(tracer, "alert_combined_match",
                        lambda *a, **k: calls.append(a) or True)
    return calls


def _write_candidates(tmp_path, monkeypatch, candidates):
    monkeypatch.setattr(tracer, "DATA_DIR", tmp_path)
    (tmp_path / "candidates").mkdir(parents=True, exist_ok=True)
    (tmp_path / "candidates" / "latest.json").write_text(
        json.dumps({"candidates": candidates}))


def test_combined_alert_refuses_a_style_vetoed_candidate(tmp_path, monkeypatch,
                                                         captured, eff):
    """README: 'a style-vetoed wallet cannot be promoted on ANY route'. The
    tracer route never checked, so the false positive the vetoes exist to stop
    reached the inbox sideways."""
    _write_candidates(tmp_path, monkeypatch, [{
        "wallet": W1, "best_score": 0.75, "latest_score": 0.75,
        "latest_evidence": {"vetoes": ["Decision frequency 50x apart"]},
    }])
    tracer._crossref_findings_with_candidates(
        [{"destination": W1, "deposited_to_hl": True, "amount_usdc": "500000"}], eff)
    assert captured == []


def test_combined_alert_refuses_a_candidate_whose_score_has_decayed(
        tmp_path, monkeypatch, captured, eff):
    """best_score 0.7113 / latest 0.1322 is real live data. The email asserts
    'matches the behavioral fingerprint' — it must be true when sent."""
    _write_candidates(tmp_path, monkeypatch, [{
        "wallet": W1, "best_score": 0.7113, "latest_score": 0.1322,
        "latest_evidence": {"vetoes": []},
    }])
    tracer._crossref_findings_with_candidates(
        [{"destination": W1, "deposited_to_hl": True, "amount_usdc": "500000"}], eff)
    assert captured == []


def test_combined_alert_fires_for_a_clean_current_match(tmp_path, monkeypatch,
                                                        captured, eff):
    """The route must still work — this is the strongest signal the system has."""
    _write_candidates(tmp_path, monkeypatch, [{
        "wallet": W1, "best_score": 0.75, "latest_score": 0.70,
        "latest_evidence": {"vetoes": []},
    }])
    tracer._crossref_findings_with_candidates(
        [{"destination": W1, "deposited_to_hl": True, "amount_usdc": "500000"}], eff)
    assert len(captured) == 1
    assert captured[0][0] == W1
    assert captured[0][1] == pytest.approx(0.70)  # reports the CURRENT score


def test_combined_alert_uses_the_same_gate_as_the_scanner(eff):
    """scanner.py alerts a deposited_to_hl candidate at eff['medium']. The tracer
    implements the same rule and must not drift from it again."""
    assert th.combined_alert_ok(eff["medium"], eff, [], route="deposited_to_hl")
    assert not th.combined_alert_ok(eff["medium"] - 0.0001, eff, [],
                                    route="deposited_to_hl")
    assert not th.combined_alert_ok(0.99, eff, ["veto"], route="deposited_to_hl")


def test_combined_alert_route_thresholds_match_the_scanner_branches(eff):
    """bridge_depositor is held to `high`, correlation only to `low` — preserve
    the per-route bars the scanner already applies."""
    assert th.combined_alert_ok(eff["high"], eff, [], route="bridge_depositor")
    assert not th.combined_alert_ok(eff["medium"], eff, [], route="bridge_depositor")
    assert th.combined_alert_ok(eff["low"], eff, [], route="correlation")


def test_scanner_route_selection_keeps_its_documented_precedence():
    """A wallet that deposited to HL is judged on that route even when it was
    also reached as an hl_transfer — the original elif-chain's ordering."""
    from src import scanner
    assert scanner._combined_route({"deposited_to_hl": True, "amount": "1", "method": "m"},
                                   "hl_transfer")[0] == "deposited_to_hl"
    assert scanner._combined_route({}, "bridge_depositor")[0] == "bridge_depositor"
    assert scanner._combined_route({}, "hl_transfer")[0] == "hl_transfer"
    assert scanner._combined_route({}, "known_linked")[0] == "known_linked"
    assert scanner._combined_route({}, "correlation")[0] == "correlation"
    assert scanner._combined_route({}, "targeted") is None


def test_every_scanner_route_has_a_declared_threshold():
    """Route names must stay in step with COMBINED_ROUTE_THRESHOLDS, or
    combined_alert_ok raises instead of silently alerting."""
    from src import scanner
    for source in ("bridge_depositor", "hl_transfer", "known_linked", "correlation"):
        route = scanner._combined_route({}, source)[0]
        assert route in th.COMBINED_ROUTE_THRESHOLDS
    assert scanner._combined_route({"deposited_to_hl": True, "amount": "1", "method": "m"},
                                   "x")[0] in th.COMBINED_ROUTE_THRESHOLDS


# --- transfer graph corroboration ------------------------------------------------

def test_transfer_graph_corroborates_at_the_resolved_gate(eff):
    """A WATCH_CLOSELY wallet counted for nothing because 0.6463 < 0.65."""
    ev = {"behavioural_score": eff["medium"], "direct_from_target": True,
          "transfer_count": 1, "depth": 1}
    scored, reasons = tg.score_confidence(ev, eff)
    bare, _ = tg.score_confidence({k: v for k, v in ev.items()
                                   if k != "behavioural_score"}, eff)
    assert scored > bare
    assert any("behavioural" in r.lower() or "trades like" in r.lower() for r in reasons)


def test_transfer_graph_classification_corroborates_at_the_resolved_gate(eff):
    """classify_node's corroboration gate drives promotion to the two highest
    tiers; it must use the same boundary as everything else."""
    ev = {"behavioural_score": eff["medium"], "direct_from_target": True}
    assert tg.classify_node(ev, 0.65, eff) == tg.CLASS_MIGRATION_CANDIDATE
    weak = {"behavioural_score": eff["medium"] - 0.05, "direct_from_target": True}
    assert tg.classify_node(weak, 0.65, eff) != tg.CLASS_MIGRATION_CANDIDATE


def test_transfer_graph_reads_the_candidates_current_score(tmp_path, monkeypatch):
    """The graph asserts 'Trades like the target (behavioural similarity X%)' in
    a CRITICAL email; X must be current."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "candidates").mkdir(parents=True)
    (tmp_path / "candidates" / "latest.json").write_text(json.dumps({"candidates": [
        {"wallet": W1, "best_score": 0.7505, "latest_score": 0.6117, "status": "ACTIVE"},
    ]}))
    scores, active = tg._load_behavioural_scores()
    assert scores[W1] == pytest.approx(0.6117)
    assert W1 in active
