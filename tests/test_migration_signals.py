# tests/test_migration_signals.py
"""Tests for the migration-detection upgrades: deposit/withdrawal correlation
(FIFO amount+time matching), L1 clustering linkage, and the unified risk score."""

from src import correlator
from src import linkage
from src import risk

DAY = 86400
T = "0x45d26f28196d226497130c4bac709d808fed4029"
W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


# --- correlator ---------------------------------------------------------------

def test_uniqueness_penalizes_round_numbers():
    assert correlator._uniqueness(1_000_000) < correlator._uniqueness(1_234_567)
    assert correlator._uniqueness(1_234_567) == 1.0
    assert correlator._uniqueness(500_000) > correlator._uniqueness(1_000_000)


def test_correlation_matches_close_amount_soon_after():
    exits = [{"amount": 1_234_567, "ts": 1000, "source": "hl_withdraw", "ref": "0xa"}]
    entries = [{"wallet": W1, "amount": 1_234_000, "ts": 1000 + 2 * DAY}]
    out = correlator.find_correlations(exits, entries, tol_pct=0.03, window_days=14,
                                       min_amount=100_000, min_confidence=0.55)
    assert len(out) == 1
    assert out[0]["wallet"] == W1
    assert out[0]["confidence"] >= 0.85  # near-exact odd amount, quick re-entry


def test_correlation_rejects_out_of_window_and_out_of_tolerance():
    exits = [{"amount": 1_000_000, "ts": 1000}]
    # too late
    late = correlator.find_correlations(
        exits, [{"wallet": W1, "amount": 1_000_000, "ts": 1000 + 30 * DAY}],
        window_days=14, min_amount=100_000)
    assert late == []
    # amount too far off
    far = correlator.find_correlations(
        exits, [{"wallet": W1, "amount": 1_200_000, "ts": 1000 + DAY}],
        tol_pct=0.03, min_amount=100_000)
    assert far == []


def test_correlation_fifo_consumes_exit_once():
    exits = [{"amount": 1_234_567, "ts": 1000}]
    entries = [
        {"wallet": W1, "amount": 1_234_567, "ts": 1000 + DAY},
        {"wallet": W2, "amount": 1_234_567, "ts": 1000 + 2 * DAY},
    ]
    out = correlator.find_correlations(exits, entries, min_amount=100_000, min_confidence=0.5)
    assert len(out) == 1  # single exit can only re-link to one deposit


def test_correlation_ignores_below_min_amount():
    out = correlator.find_correlations(
        [{"amount": 5000, "ts": 1000}],
        [{"wallet": W1, "amount": 5000, "ts": 1000 + DAY}],
        min_amount=100_000)
    assert out == []


# --- linkage ------------------------------------------------------------------

def test_linkage_direct_funding_by_target():
    out = linkage.compute_linkage(W1, T, set(), T, None, set(), excluded=set())
    assert out["shared_funder"] is True
    assert out["linkage_bonus"] >= 0.15


def test_linkage_shared_deposit_address_is_strongest():
    cex = "0xdeadbeef00000000000000000000000000000000"
    out = linkage.compute_linkage(W1, None, {cex}, T, None, {cex}, excluded=set())
    assert out["shared_deposit_addresses"] == [cex]
    assert out["linkage_bonus"] >= 0.18


def test_linkage_excluded_addresses_ignored():
    cex = "0xdeadbeef00000000000000000000000000000000"
    out = linkage.compute_linkage(W1, cex, {cex}, T, cex, {cex}, excluded={cex})
    assert out["shared_funder"] is False
    assert out["shared_deposit_addresses"] == []
    assert out["linkage_bonus"] == 0.0


def test_linkage_bonus_capped():
    cex = "0xcexdeposit000000000000000000000000000000"
    out = linkage.compute_linkage(W1, T, {cex}, T, None, {cex}, excluded=set())
    assert out["linkage_bonus"] <= 0.30


# --- risk score ---------------------------------------------------------------

def test_risk_low_when_quiet():
    r = risk.compute_risk_score({})
    assert r["score"] == 0.0
    assert r["level"] == "LOW"


def test_risk_critical_when_everything_fires():
    r = risk.compute_risk_score({
        "days_silent": 12, "drawdown_pct": 0.6, "l1_outbound": True,
        "hl_native_outbound": True, "top_candidate_score": 0.95,
        "correlation_confidence": 0.9, "linkage_hit": True, "xyz_abandoned": True,
        "top_candidate_wallet": W1,
    })
    assert r["score"] >= 75
    assert r["level"] == "CRITICAL"
    assert r["top_candidate_wallet"] == W1


def test_risk_top_candidate_below_threshold_scores_zero_there():
    r = risk.compute_risk_score({"top_candidate_score": 0.60})
    assert all(f["signal"] != "top_candidate" for f in r["factors"])


def test_risk_levels_monotonic():
    mild = risk.compute_risk_score({"days_silent": 3})["score"]
    strong = risk.compute_risk_score({"days_silent": 3, "top_candidate_score": 0.9,
                                      "correlation_confidence": 0.8})["score"]
    assert strong > mild


def test_xyz_abandoned_detection():
    from src.utils import now_ms
    old = now_ms() - 20 * 86_400_000
    recent = now_ms() - 1 * 86_400_000
    assert risk._xyz_abandoned([{"coin": "xyz:SP500", "time": old}]) is True
    assert risk._xyz_abandoned([{"coin": "xyz:SP500", "time": recent}]) is False
    assert risk._xyz_abandoned([{"coin": "BTC", "time": old}]) is False  # never traded xyz
