# tests/test_market_rarity.py
"""Tests for measured market-rarity calibration.

Replaces a flat +0.12 for any shared xyz: market. That constant assumed HIP-3
markets are near-unique; measurement disproved it (xyz:BRENTOIL is traded by ~20%
of scanned wallets) and the bonus lifted three style-vetoed wallets above the
target's own self-match.
"""

import json

from src import calibration as c
from src import thresholds as th
from src.scanner import build_candidate_fingerprint, compute_similarity
from tests.test_style_matching import DAY_MS, scalper_fills, swing_fills

EFF = {"high": 0.52, "medium": 0.47, "low": 0.42}


def freq_table(eligible: int, counts: dict) -> dict:
    """One synthetic observation collapsed into the summary shape."""
    return c.summarise_market_observations([{
        "recorded_at": "2026-07-27T00:00:00+00:00",
        "eligible_wallets": eligible,
        "market_counts": counts,
    }])


# --- the formula ------------------------------------------------------------------

def test_common_market_earns_nothing():
    """xyz:BRENTOIL: measured at ~20% of scanned wallets. Sharing it is not evidence."""
    f = freq_table(100, {"xyz:BRENTOIL": 20})
    assert c.market_frequency("xyz:BRENTOIL", f) > c.COMMON_FREQUENCY
    bonus, reasons = c.market_rarity_bonus(["xyz:BRENTOIL"], f)
    assert bonus == 0.0
    assert any("too common" in r for r in reasons)


def test_genuinely_rare_market_earns_a_real_bonus():
    f = freq_table(1000, {"xyz:UNOBTAINIUM": 2})   # 0.2% of wallets
    bonus, reasons = c.market_rarity_bonus(["xyz:UNOBTAINIUM"], f)
    assert bonus > 0.09
    assert bonus <= c.MAX_MARKET_BONUS
    assert any("rarity bonus" in r for r in reasons)


def test_bonus_is_monotonic_in_rarity():
    """Rarer must never score lower than more common."""
    prev = -1.0
    for hits in (200, 100, 40, 20, 10, 5, 2, 1):
        f = freq_table(1000, {"xyz:M": hits})
        bonus, _ = c.market_rarity_bonus(["xyz:M"], f)
        assert bonus >= prev, f"non-monotonic at {hits} hits"
        prev = bonus


def test_tiny_sample_gets_no_bonus():
    """One wallet in a one-wallet sample must not read as conclusive."""
    f = freq_table(3, {"xyz:SEEN_ONCE": 1})
    assert f["sufficient"] is False
    bonus, reasons = c.market_rarity_bonus(["xyz:SEEN_ONCE"], f)
    assert bonus == 0.0
    assert any("rarity as unproven" in r for r in reasons)


def test_zero_observations_defaults_conservatively():
    """No data must mean no bonus — never the maximum."""
    empty = c.summarise_market_observations([])
    assert empty["eligible_wallets"] == 0
    bonus, reasons = c.market_rarity_bonus(["xyz:ANYTHING"], empty)
    assert bonus == 0.0
    assert reasons


def test_never_observed_market_in_large_sample_is_smoothed_not_infinite():
    """A market with zero hits must not read as frequency 0 (infinitely rare)."""
    f = freq_table(500, {"BTC": 500})
    freq = c.market_frequency("xyz:NEVER_SEEN", f)
    assert freq > 0, "add-one smoothing must keep frequency positive"
    bonus, _ = c.market_rarity_bonus(["xyz:NEVER_SEEN"], f)
    assert 0 < bonus <= c.MAX_MARKET_BONUS


def test_multiple_shared_markets_compound_with_diminishing_returns():
    f = freq_table(1000, {"xyz:A": 2, "xyz:B": 3, "xyz:C": 4})
    one, _ = c.market_rarity_bonus(["xyz:A"], f)
    three, reasons = c.market_rarity_bonus(["xyz:A", "xyz:B", "xyz:C"], f)
    assert three > one                       # more shared rare markets is stronger
    assert three < one * 3                   # but with diminishing returns
    assert three <= c.MAX_MARKET_BONUS       # and capped
    assert len(reasons) == 3


def test_common_markets_never_accumulate_into_a_bonus():
    """Many mildly-popular markets must not add up to a real signal."""
    counts = {f"xyz:M{i}": 20 for i in range(8)}
    f = freq_table(100, counts)
    bonus, _ = c.market_rarity_bonus(list(counts), f)
    assert bonus == 0.0


# --- integration with scoring -----------------------------------------------------

def test_vetoed_candidate_gets_no_market_bonus():
    """A style veto means "different human"; boosting similarity is incoherent."""
    f = freq_table(1000, {"xyz:RARE": 2})
    swing = build_candidate_fingerprint(swing_fills(coin="xyz:RARE"), {})
    scalp = build_candidate_fingerprint(scalper_fills(coin="xyz:RARE"), {})
    score, _, ev = compute_similarity(swing, scalp, EFF, f)
    assert ev["vetoes"]
    assert ev["market_rarity"]["bonus_applied"] == 0.0
    assert score <= th.VETO_SCORE_CAP
    assert any("withheld" in r for r in ev["market_rarity"]["explanations"])


def test_rare_market_only_candidate_stays_below_high_confidence():
    """A rare market must corroborate a match, never manufacture one."""
    # Score sits just under the high threshold before the bonus, over it after.
    without = EFF["high"] - 0.02
    with_bonus = EFF["high"] + 0.05
    d = th.disposition(with_bonus, EFF, rare_overlap=True,
                       score_without_market_bonus=without)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("only via the shared-market bonus" in b for b in d["blockers"])
    assert d["reasons"]  # still visible, with its reason recorded


def test_rare_market_plus_independent_evidence_can_promote():
    """When behaviour alone already clears the bar, the market corroborates it."""
    d = th.disposition(EFF["high"] + 0.05, EFF, rare_overlap=True,
                       score_without_market_bonus=EFF["high"] + 0.01)
    assert d["action"] == th.ACTION_ALERT
    assert any("corroborated by shared rare markets" in r for r in d["reasons"])


def test_evidence_publishes_frequency_bonus_and_explanation():
    """The dashboard and any reviewer must be able to audit the bonus."""
    f = freq_table(1000, {"xyz:RARE": 2})
    a = build_candidate_fingerprint(swing_fills(coin="xyz:RARE"), {})
    b = build_candidate_fingerprint(
        swing_fills(coin="xyz:RARE", start=1_700_000_000_000 + 30 * DAY_MS), {})
    score, _, ev = compute_similarity(a, b, EFF, f)
    mr = ev["market_rarity"]
    assert mr["shared_markets"] == ["xyz:RARE"]
    assert mr["bonus_applied"] > 0
    assert mr["score_without_bonus"] < score
    assert mr["frequencies"]["xyz:RARE"] > 0
    assert mr["explanations"]
    assert any("rarity bonus" in r for r in ev["reasons"])


def test_common_market_does_not_change_the_score():
    f = freq_table(100, {"xyz:BRENTOIL": 20})
    a = build_candidate_fingerprint(swing_fills(coin="xyz:BRENTOIL"), {})
    b = build_candidate_fingerprint(
        swing_fills(coin="xyz:BRENTOIL", start=1_700_000_000_000 + 30 * DAY_MS), {})
    score, _, ev = compute_similarity(a, b, EFF, f)
    assert ev["market_rarity"]["bonus_applied"] == 0.0
    assert ev["market_rarity"]["score_without_bonus"] == score


# --- persistence ------------------------------------------------------------------

def test_observations_are_bounded_by_count_and_age(tmp_path, monkeypatch):
    p = tmp_path / "market_frequency.json"
    monkeypatch.setattr(c, "MAX_MARKET_OBSERVATIONS", 5)
    for i in range(12):
        c.record_market_observation({f"0x{i:040x}": ["xyz:A", "BTC"]}, path=p)
    kept = json.loads(p.read_text())["observations"]
    assert len(kept) == 5, "rolling window not bounded by count"

    # An observation older than the window must be dropped on the next write.
    stale = {"recorded_at": "2020-01-01T00:00:00+00:00",
             "eligible_wallets": 999, "market_counts": {"xyz:OLD": 999}}
    data = json.loads(p.read_text())
    data["observations"] = [stale, *data["observations"]]
    p.write_text(json.dumps(data))
    c.record_market_observation({"0xnew": ["BTC"]}, path=p)
    kept = json.loads(p.read_text())["observations"]
    assert all(o["recorded_at"] > "2021" for o in kept), "aged-out observation retained"


def test_round_trip_summary_matches_written_data(tmp_path):
    p = tmp_path / "mf.json"
    c.record_market_observation({"0xa": ["xyz:R", "BTC"], "0xb": ["BTC"]}, path=p)
    c.record_market_observation({"0xc": ["xyz:R"]}, path=p)
    s = c.load_market_frequencies(p)
    assert s["eligible_wallets"] == 3
    assert s["market_counts"]["xyz:R"] == 2
    assert s["market_counts"]["BTC"] == 2
    assert s["observations"] == 2


def test_empty_observation_does_not_corrupt_existing_data(tmp_path):
    p = tmp_path / "mf.json"
    c.record_market_observation({"0xa": ["xyz:R"]}, path=p)
    before = c.load_market_frequencies(p)
    after = c.record_market_observation({}, path=p)
    assert after["eligible_wallets"] == before["eligible_wallets"]
