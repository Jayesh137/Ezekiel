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


# --- garbage must never read as confidence ----------------------------------------

def test_a_non_finite_score_is_not_maximum_confidence(eff):
    """NaN survives json.load, and NaN comparisons are all False, so the clamp
    max(0, min(1, nan)) returned 1.0 — a corrupt score scored full marks. For a
    system whose job is to be trusted, garbage must read as no-evidence."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert th.behavioural_strength(bad, eff) == 0.0
    r = risk.compute_risk_score({"top_candidate_score": float("nan")}, eff)
    assert all(f["signal"] != "top_candidate" for f in r["factors"])
    assert r["score"] == 0.0


def test_candidate_current_score_rejects_non_finite_values():
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert candidate_current_score({"latest_score": bad}) == 0.0
    assert candidate_current_score({"latest_score": "0.7"}) == pytest.approx(0.7)
    assert candidate_current_score({"latest_score": "not-a-number"}) == 0.0


def test_behavioural_scores_survive_a_wrongly_shaped_file(tmp_path, monkeypatch):
    """save_latest writes dict OR list, and data/portfolio/latest.json really is
    a list. A list where a dict was expected raised AttributeError straight out
    of the except clause and killed the whole transfer-graph job."""
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    (tmp_path / "candidates").mkdir(parents=True)
    for body in ("[1,2,3]", '"a string"', "null", "{}"):
        (tmp_path / "candidates" / "latest.json").write_text(body)
        scores, active = tg._load_behavioural_scores()
        assert scores == {} and active == set()


# --- decision frequency: intensity, not presence ----------------------------------
#
# On 2026-08-05 the live self-match FAILED: the target scored 0.45 against his own
# history, ranked 8th behind strangers, and tripped his own style veto with
# "Decision frequency 6x apart (0.17 vs 0.94 position episodes/day)". That put the
# scanner into OBSERVING and switched behavioural alerting off in production.
#
# The two windows held the SAME number of active trading days (12 each). They
# differed only in how far apart those days were spread — 19 calendar days in the
# older window, 72 in the recent one. episodes_per_day divided by calendar span,
# so a trader who kept trading the same way but showed up less often read as a
# different human. active_days_ratio already measures presence; dividing episode
# counts by span counted it a second time, and as a hard veto.

# A UTC midnight, so a session sits inside one day bucket. active_days counts
# distinct UTC days, so a session straddling midnight would count as two and the
# fixture would measure the fixture rather than the code.
_MIDNIGHT = 19675 * 86_400_000


def _sessions(day_offsets, episodes_per_session=3, start=_MIDNIGHT):
    """Identical trading sessions placed on the given days, 08:00-12:00 UTC."""
    from tests.test_style_matching import episode_fills
    fills = []
    for d in day_offsets:
        for i in range(episodes_per_session):
            t = start + d * 86_400_000 + (8 + i) * 3_600_000
            fills.extend(episode_fills("ETH", t, hold_min=120, long=(i % 2 == 0)))
    return fills


def _style(fills):
    from src.fingerprint import compute_style_profile
    return {"style_profile": compute_style_profile(fills)}


def test_decision_frequency_measures_intensity_not_presence():
    """12 sessions packed into 19 days vs the same 12 spread over 72. Same trader,
    same behaviour when trading — taking a break is regime, not identity."""
    from src.scanner import check_style_vetoes
    dense = _style(_sessions(list(range(12))))
    sparse = _style(_sessions([d * 6 for d in range(12)]))
    assert check_style_vetoes(dense, sparse) == []
    assert check_style_vetoes(sparse, dense) == []


def test_decision_frequency_is_per_active_day():
    from src.fingerprint import compute_style_profile
    dense = compute_style_profile(_sessions(list(range(12))))
    sparse = compute_style_profile(_sessions([d * 6 for d in range(12)]))
    assert dense["activity"]["episodes_per_active_day"] == pytest.approx(
        sparse["activity"]["episodes_per_active_day"], rel=0.05)
    # Presence is still measured — just once, and not as a veto.
    assert dense["activity"]["active_days_ratio"] > sparse["activity"]["active_days_ratio"]
    assert dense["activity"]["active_days_ratio"] <= 1.0


def test_activity_metrics_do_not_mix_units_with_an_old_fingerprint():
    """The key was renamed deliberately. A fingerprint written before the change
    holds calendar-normalised numbers; comparing those against per-active-day ones
    would be worse than not comparing at all, so the dimension must drop out."""
    from src.scanner import check_style_vetoes, compare_activity
    old = {"style_profile": {"sufficient_data": True,
                             "activity": {"episodes_per_day": 0.17, "fills_per_day": 3.0}}}
    new = _style(_sessions(list(range(12))))
    assert compare_activity(old["style_profile"], new["style_profile"]) is None
    assert check_style_vetoes(old, new) == []


def test_a_genuine_frequency_difference_still_vetoes():
    """The veto must keep its teeth: a scalper taking 60 round-trips a session is
    not the same person as a swing trader taking 3."""
    from src.scanner import check_style_vetoes
    from tests.test_style_matching import scalper_fills, swing_fills
    swing = _style(swing_fills())
    scalp = _style(scalper_fills())
    assert any("Decision frequency" in v for v in check_style_vetoes(swing, scalp))


def test_activity_similarity_survives_a_trading_break():
    """The soft dimension should also stop punishing intermittency three times —
    presence is still carried by active_days_ratio."""
    from src.scanner import compare_activity
    dense = _style(_sessions(list(range(12))))["style_profile"]
    sparse = _style(_sessions([d * 6 for d in range(12)]))["style_profile"]
    assert compare_activity(dense, sparse) > 0.5


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
