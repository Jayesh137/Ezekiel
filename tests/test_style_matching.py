# tests/test_style_matching.py
"""Tests for style-aware matching: style profile computation, hard vetoes,
None-dimension renormalization, population calibration, and the self-match
backtest windowing. Motivated by a false positive where a wallet with a
completely different trading style scored as a match."""

import numpy as np

from src import calibration
from src.backtest import split_windows, MIN_WINDOW_FILLS
from src.fingerprint import compute_style_profile, _position_episodes
from src.scanner import (
    build_candidate_fingerprint, compute_similarity, check_style_vetoes,
    compare_activity, compare_direction_bias, VETO_SCORE_CAP,
)

DAY_MS = 86_400_000
MIN_MS = 60_000


def episode_fills(coin, t_open_ms, hold_min, sz=1.0, px=100.0, long=True,
                  pnl=10.0, twap=None):
    """One open->close position episode (two fills)."""
    d_open, d_close = ("Open Long", "Close Long") if long else ("Open Short", "Close Short")
    s_open, s_close = ("B", "A") if long else ("A", "B")
    signed = sz if long else -sz
    return [
        {"coin": coin, "px": str(px), "sz": str(sz), "side": s_open,
         "time": t_open_ms, "startPosition": "0.0", "dir": d_open,
         "closedPnl": "0.0", "crossed": True, "fee": "0.1", "twapId": twap},
        {"coin": coin, "px": str(px), "sz": str(sz), "side": s_close,
         "time": t_open_ms + hold_min * MIN_MS, "startPosition": str(signed),
         "dir": d_close, "closedPnl": str(pnl), "crossed": True, "fee": "0.1",
         "twapId": twap},
    ]


def swing_fills(days=21, per_day=2, hold_min=300, start=1_700_000_000_000, coin="ETH"):
    """Swing style: few trades/day, multi-hour holds, mixed long/short."""
    fills = []
    for d in range(days):
        for i in range(per_day):
            t = start + d * DAY_MS + i * 6 * 60 * MIN_MS
            fills.extend(episode_fills(coin, t, hold_min, long=(i % 3 != 0),
                                       pnl=25.0 if i % 2 else -12.0))
    return fills


def scalper_fills(days=21, per_day=60, hold_min=20, start=1_700_000_000_000, coin="ETH"):
    """Scalper style: dozens of trades/day, sub-hour holds, long-only."""
    fills = []
    for d in range(days):
        for i in range(per_day):
            t = start + d * DAY_MS + i * 22 * MIN_MS
            fills.extend(episode_fills(coin, t, hold_min, long=True,
                                       pnl=2.0 if i % 2 else -2.5))
    return fills


# --- style profile -------------------------------------------------------------

def test_style_profile_activity_and_direction():
    sp = compute_style_profile(swing_fills())
    assert sp["sufficient_data"]
    assert 3.0 < sp["activity"]["fills_per_day"] < 5.0
    assert sp["direction"]["total_opens"] == 42
    # per_day=2 with long=(i % 3 != 0) → i=0 short, i=1 long → 50% long opens
    assert abs(sp["direction"]["long_open_pct"] - 0.5) < 0.05


def test_style_profile_episodes_and_loss_handling():
    sp = compute_style_profile(swing_fills())
    assert sp["position_management"]["episodes"] == 42
    assert sp["position_management"]["mean_fills_per_episode"] == 2.0
    lh = sp["loss_handling"]
    assert lh["closed_wins"] == 21 and lh["closed_losses"] == 21
    assert lh["win_loss_magnitude_ratio"] > 1.5  # 25 vs 12 magnitude


def test_style_profile_execution_ratios():
    fills = swing_fills()
    fills[0]["twapId"] = 123
    fills.append({"coin": "@107", "px": "40", "sz": "10", "side": "B",
                  "time": fills[-1]["time"] + 1, "dir": "Buy", "closedPnl": "0.0",
                  "crossed": True, "twapId": None})
    sp = compute_style_profile(fills)
    assert sp["execution"]["twap_ratio"] > 0
    assert sp["execution"]["spot_fill_ratio"] > 0


def test_position_episodes_ignore_spot():
    fills = [{"coin": "@107", "px": "40", "sz": "10", "side": "B", "time": 1,
              "dir": "Buy", "closedPnl": "0.0"}]
    assert _position_episodes(fills) == []


def test_insufficient_data_flag():
    sp = compute_style_profile(swing_fills()[:6])
    assert sp["sufficient_data"] is False


# --- comparisons return None without data --------------------------------------

def test_comparisons_none_when_data_missing():
    sp = compute_style_profile(swing_fills())
    assert compare_activity({}, sp) is None
    assert compare_direction_bias({}, sp) is None
    assert compare_direction_bias({"direction": {"total_opens": 5, "long_open_pct": 1.0}}, sp) is None


# --- vetoes ---------------------------------------------------------------------

def _fp(fills):
    return build_candidate_fingerprint(fills, {})


def test_scalper_vs_swing_is_vetoed_and_capped():
    swing, scalp = _fp(swing_fills()), _fp(scalper_fills())
    vetoes = check_style_vetoes(swing, scalp)
    assert vetoes  # frequency and hold-style incompatibility
    score, dims, evidence = compute_similarity(swing, scalp)
    assert score <= VETO_SCORE_CAP
    assert evidence["vetoes"]
    assert evidence["tier"] == "BACKGROUND"


def test_direction_flip_is_not_vetoed():
    """The target flips long/short with market regime — the same human must
    never be vetoed for direction (proven by the self-match backtest)."""
    mixed = _fp(swing_fills())  # 50% short opens
    lo_fills = []
    start = 1_700_000_000_000
    for d in range(21):
        for i in range(2):
            t = start + d * DAY_MS + i * 6 * 60 * MIN_MS
            lo_fills.extend(episode_fills("ETH", t, 300, long=True,
                                          pnl=25.0 if i % 2 else -12.0))
    long_only = _fp(lo_fills)
    assert check_style_vetoes(mixed, long_only) == []


def test_same_style_not_vetoed_and_outscores_different_style():
    a = _fp(swing_fills(start=1_700_000_000_000))
    b = _fp(swing_fills(start=1_700_000_000_000 + 30 * DAY_MS))
    assert check_style_vetoes(a, b) == []
    same_score, _, ev = compute_similarity(a, b)
    diff_score, _, _ = compute_similarity(a, _fp(scalper_fills()))
    assert same_score > 0.75
    assert same_score > diff_score
    assert ev["vetoes"] == []


def test_no_veto_on_thin_data():
    swing = _fp(swing_fills())
    thin = _fp(scalper_fills()[:10])  # insufficient_data
    assert check_style_vetoes(swing, thin) == []


# --- None renormalization -------------------------------------------------------

def test_missing_style_profile_renormalizes_instead_of_zeroing():
    a = _fp(swing_fills())
    b = _fp(swing_fills(start=1_700_000_000_000 + 30 * DAY_MS))
    b_no_style = dict(b)
    b_no_style["style_profile"] = {}
    score_with, _, _ = compute_similarity(a, b)
    score_without, dims, _ = compute_similarity(a, b_no_style)
    assert dims["activity"] is None
    # Dropping style dims must not tank the score to zero
    assert score_without > 0.5
    assert abs(score_with - score_without) < 0.25


# --- calibration ----------------------------------------------------------------

def test_percentile_gate_open_with_small_population():
    assert calibration.score_percentile(0.9, [0.5] * 10) is None
    assert calibration.passes_percentile_gate(0.9, [0.5] * 10)


def test_percentile_gate_blocks_common_scores():
    pop = list(np.linspace(0.5, 0.95, 200))
    assert not calibration.passes_percentile_gate(0.7, pop)
    assert calibration.passes_percentile_gate(0.99, pop)


def test_record_and_load_population(tmp_path, monkeypatch):
    monkeypatch.setattr(calibration, "POPULATION_PATH", tmp_path / "population.json")
    n = calibration.record_population_scores([0.5, 0.6, 0.7])
    assert n == 3
    assert calibration.load_population() == [0.5, 0.6, 0.7]
    calibration.record_population_scores([0.8])
    assert len(calibration.load_population()) == 4


# --- adaptive thresholds ----------------------------------------------------------

RAW_THRESHOLDS = {"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65}


def test_effective_thresholds_lowered_by_backtest_ceiling(tmp_path, monkeypatch):
    import json as _json
    from src import scanner
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "backtest.json").write_text(
        _json.dumps({"passed": True, "self_score": 0.52}))
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path / "data")
    eff = scanner._effective_thresholds(RAW_THRESHOLDS)
    assert eff["high"] == 0.50  # self-score - 0.02
    assert eff["high"] > scanner.VETO_SCORE_CAP
    assert eff["medium"] == scanner.VETO_SCORE_CAP  # floored
    assert eff["low"] == 0.40
    assert eff["source"] == "backtest_adapted"


def test_effective_thresholds_stay_reachable_for_true_trader():
    """The adapted high threshold must never exceed what the target scores
    against his own history — that would guarantee missing the migration."""
    from src import thresholds as th
    for ceiling in (0.48, 0.53, 0.62, 0.80):
        eff = th.resolve(RAW_THRESHOLDS, {"passed": True, "self_score": ceiling})
        assert eff["high"] <= max(ceiling, th.MIN_HIGH), (
            f"high {eff['high']} unreachable at ceiling {ceiling}")


def test_effective_thresholds_unchanged_when_backtest_fails(tmp_path, monkeypatch):
    import json as _json
    from src import scanner
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir()
    (profile_dir / "backtest.json").write_text(
        _json.dumps({"passed": False, "self_score": 0.3}))
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path / "data")
    eff = scanner._effective_thresholds(RAW_THRESHOLDS)
    assert (eff["high"], eff["medium"], eff["low"]) == (0.90, 0.80, 0.65)
    assert eff["source"] == "config"


def test_missing_backtest_report_falls_back_visibly(tmp_path, capsys):
    """A missing/corrupt report must warn, not silently install unreachable
    thresholds — this was previously swallowed by a bare `except: pass`."""
    from src import thresholds as th
    assert th.load_backtest_report(tmp_path) is None
    assert "WARNING" in capsys.readouterr().out
    (tmp_path / "backtest.json").write_text("{not json")
    assert th.load_backtest_report(tmp_path) is None
    assert "WARNING" in capsys.readouterr().out


def test_vetoed_wallet_never_behavioral_alerts():
    from src.scanner import _should_alert_behavioral
    assert not _should_alert_behavioral(
        "0xabc", 0.95, {"high": 0.5, "medium": 0.45, "low": 0.4},
        population=[0.1] * 100, vetoes=["Decision frequency 20x apart"])


# --- veto applies to EVERY alert route, not just the behavioural one -------------

def test_veto_blocks_all_alert_routes_but_keeps_candidate_visible():
    """A style-vetoed wallet must not be promoted by any route, including the
    bonus-lifted ones — but its evidence must survive on the watchlist."""
    from src import thresholds as th
    vetoes = ["Decision frequency 20x apart"]
    assert th.can_alert(vetoes) is False
    assert th.can_alert([]) is True
    assert th.can_alert(None) is True

    # Bonus-lifted vetoed score can exceed the alert threshold; disposition must
    # still refuse promotion while keeping the wallet watchlisted.
    eff = {"high": 0.51, "medium": 0.46, "low": 0.41}
    d = th.disposition(th.VETO_BONUS_CEILING, eff, vetoes=vetoes, rare_overlap=True)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("veto" in b for b in d["blockers"])
    assert d["reasons"]  # kept for a reason, and the reason is recorded


def test_disposition_watchlists_suppressed_candidates():
    from src import thresholds as th
    eff = {"high": 0.51, "medium": 0.46, "low": 0.41}
    # Clears high but fails the percentile gate -> watchlisted, not discarded.
    d = th.disposition(0.72, eff, percentile_ok=False)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("percentile" in b for b in d["blockers"])
    # Fails persistence -> still watchlisted.
    d2 = th.disposition(0.72, eff, sustained=False)
    assert d2["action"] == th.ACTION_WATCHLIST
    # Genuinely unremarkable -> background.
    assert th.disposition(0.10, eff)["action"] == th.ACTION_BACKGROUND
    # Clean and clearing everything -> promoted.
    assert th.disposition(0.72, eff)["action"] == th.ACTION_ALERT


def test_tier_labels_track_effective_thresholds():
    """classify() must use the same numbers alerting uses; it was hardcoded to
    0.90/0.80/0.65 while alerts ran near 0.51, so a wallet emailed as HIGH while
    displaying as WEAK_LEAD."""
    from src import thresholds as th
    eff = th.resolve(RAW_THRESHOLDS, {"passed": True, "self_score": 0.53})
    assert th.classify(0.75, eff) == th.TIER_CONFIRMED
    assert th.classify(0.10, eff) == th.TIER_BACKGROUND
    # Same score under raw config thresholds is merely a weak lead — proving the
    # two policies really do disagree and that one source now decides.
    raw = th.resolve(RAW_THRESHOLDS, None)
    assert th.classify(0.75, raw) == th.TIER_WEAK


def test_calibration_gate_observes_then_enforces():
    assert calibration.gate_active([0.5] * 10) is False
    assert calibration.gate_active([0.5] * calibration.MIN_SAMPLES_FOR_GATE) is True
    # While observing, nothing is suppressed.
    assert calibration.passes_percentile_gate(0.9, [0.5] * 10) is True


# --- backtest windowing ----------------------------------------------------------

def test_split_windows_disjoint_and_anchored():
    fills = swing_fills(days=60, per_day=2)
    older, recent = split_windows(fills)
    assert len(older) >= MIN_WINDOW_FILLS and len(recent) >= MIN_WINDOW_FILLS
    assert max(f["time"] for f in older) < min(f["time"] for f in recent)


def test_split_windows_falls_back_to_halves():
    fills = swing_fills(days=10, per_day=6)  # thin 21d windows → halve instead
    older, recent = split_windows(fills)
    assert len(older) + len(recent) == len(fills)
    assert max(f["time"] for f in older) <= min(f["time"] for f in recent)
