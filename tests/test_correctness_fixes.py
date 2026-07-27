# tests/test_correctness_fixes.py
"""Regression tests for the Critical/High defects found in the 2026-07-27 audit.

Each test pins a specific broken behaviour so it cannot silently return:
  - hold_duration scored the trader 0.0 against his own history
  - risk signals latched on permanently
  - the drop-alert cursor zeroed the drawdown signal
  - collection stalling produced no alert at all
"""

import json
from datetime import UTC

from src import heartbeat, risk
from src.fingerprint import MIN_HOLD_EPISODES, compute_hold_duration
from src.scanner import compare_hold_duration

MIN = 60_000
HOUR = 60 * MIN


def _fill(coin, t, sz, side, start_pos, dir_, pnl="0.0"):
    return {"coin": coin, "time": t, "sz": str(sz), "px": "100.0", "side": side,
            "startPosition": str(start_pos), "dir": dir_, "closedPnl": pnl,
            "crossed": True, "fee": "0.1"}


def _episode(coin, t_open, hold_ms, scale_ins=1, scale_outs=1):
    """A position episode built the way a TWAP trader actually trades: several
    scale-in fills, then several scale-out fills."""
    fills = []
    pos = 0.0
    for i in range(scale_ins):
        fills.append(_fill(coin, t_open + i, 1.0, "B", pos, "Open Long"))
        pos += 1.0
    per_out = pos / scale_outs
    for i in range(scale_outs):
        fills.append(_fill(coin, t_open + hold_ms + i, per_out, "A", pos,
                           "Close Long", pnl="5.0"))
        pos -= per_out
    return fills


# --- hold_duration: the dimension that scored the trader 0.0 vs himself ----------

def test_twap_scale_in_does_not_discard_closes():
    """The old matcher kept one open_time per coin, so a scale-in overwrote it and
    every close after the first was dropped. 20 episodes must yield 20 durations."""
    fills = []
    for d in range(20):
        fills.extend(_episode("ETH", d * 24 * HOUR, 2 * HOUR, scale_ins=5, scale_outs=4))
    hd = compute_hold_duration(fills)
    assert hd["episode_count"] == 20
    assert hd["sufficient_data"] is True
    # ~2h holds land in the 1h-4h bucket, not scattered into sub-minute noise.
    assert hd["distribution_buckets"]["1h_to_4h"] == 1.0


def test_window_starting_mid_position_still_yields_durations():
    """The recent backtest window held 2,158 'Close Short' fills against 2 opens
    because the opens fell before the boundary; the old matcher produced zero
    durations and cosine scored 0.0. Episode reconstruction uses startPosition,
    so a window that opens mid-position still measures."""
    # Orphaned closes: position already open when the window starts.
    fills = [_fill("BTC", i * HOUR, 1.0, "A", 10.0 - i, "Close Short", pnl="3.0")
             for i in range(8)]
    hd = compute_hold_duration(fills)
    assert hd["episode_count"] >= 1
    assert any(v > 0 for v in hd["distribution_buckets"].values())


def test_same_millisecond_twap_batch_is_kept_as_zero_hold():
    """`dur > 0` previously dropped TWAP batches that filled within one ms."""
    fills = []
    for d in range(MIN_HOLD_EPISODES + 1):
        t = d * 24 * HOUR
        fills.extend([
            _fill("SOL", t, 1.0, "B", 0.0, "Open Long"),
            _fill("SOL", t, 1.0, "A", 1.0, "Close Long", pnl="1.0"),
        ])
    hd = compute_hold_duration(fills)
    assert hd["episode_count"] == MIN_HOLD_EPISODES + 1
    assert hd["distribution_buckets"]["under_1h"] == 1.0


def test_thin_history_reports_insufficient_and_compares_as_none():
    """Too few episodes must exclude the dimension (weight renormalized) rather
    than score 0.0, which penalised the true trader harder than strangers."""
    thin = _episode("ETH", 0, HOUR)  # a single episode
    hd = compute_hold_duration(thin)
    assert hd["sufficient_data"] is False

    rich = []
    for d in range(20):
        rich.extend(_episode("ETH", d * 24 * HOUR, 2 * HOUR))
    fat_fp = {"hold_duration": compute_hold_duration(rich)}
    thin_fp = {"hold_duration": hd}

    assert compare_hold_duration(fat_fp, thin_fp) is None
    assert compare_hold_duration(thin_fp, fat_fp) is None
    # Two comparable sides do produce a score.
    assert compare_hold_duration(fat_fp, fat_fp) is not None


def test_self_comparison_scores_high_not_zero():
    """The headline regression: identical hold behaviour must not score 0.0."""
    fills = []
    for d in range(20):
        fills.extend(_episode("ETH", d * 24 * HOUR, 3 * HOUR, scale_ins=4, scale_outs=3))
    fp = {"hold_duration": compute_hold_duration(fills)}
    score = compare_hold_duration(fp, fp)
    assert score is not None and score > 0.95


def test_empty_fills_is_insufficient_not_zero_scoring():
    hd = compute_hold_duration([])
    assert hd["sufficient_data"] is False
    assert hd["episode_count"] == 0


# --- risk signals must expire ----------------------------------------------------

def test_l1_outbound_expires(tmp_path, monkeypatch):
    """One historical transfer previously contributed +8 points forever."""
    from datetime import datetime, timedelta
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    (tmp_path / "fund_flows").mkdir(parents=True)

    def write(days_ago):
        ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
        (tmp_path / "fund_flows" / "latest.json").write_text(json.dumps(
            {"findings": [{"amount_usdc_raw": 500000.0, "detected_at": ts}]}))

    write(1)
    assert risk._gather_signals()["l1_outbound"] is True
    write(risk.RECENT_SIGNAL_DAYS + 5)
    assert risk._gather_signals()["l1_outbound"] is False


def test_undated_finding_does_not_latch_signal(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    (tmp_path / "fund_flows").mkdir(parents=True)
    (tmp_path / "fund_flows" / "latest.json").write_text(json.dumps(
        {"findings": [{"amount_usdc_raw": 500000.0}]}))  # no detected_at
    assert risk._gather_signals()["l1_outbound"] is False


def test_drawdown_survives_drop_alert_cursor_reset(tmp_path, monkeypatch):
    """collector resets prev_account_value_cents when it fires a drop alert.
    risk must read the independent high-water cursor, or drawdown_pct collapses
    to 0.0 exactly when the account has just been wiped."""
    monkeypatch.setattr(risk, "DATA_DIR", tmp_path)
    (tmp_path / "account").mkdir(parents=True)
    (tmp_path / "account" / "latest.json").write_text(json.dumps(
        {"perp": {"marginSummary": {"accountValue": "400000"}}}))

    cursors = {
        "account_high_water_cents": 100_000_000,   # $1,000,000 high-water
        "prev_account_value_cents": 40_000_000,    # reset by the drop alert
        "last_fill_time": 0,
    }
    monkeypatch.setattr(risk, "read_cursor", lambda n: cursors.get(n, 0))
    monkeypatch.setattr(risk, "load_all_records", lambda d: [])

    signals = risk._gather_signals()
    assert signals["drawdown_pct"] > 0.55  # $1M -> $400k is a 60% drawdown


# --- heartbeat -------------------------------------------------------------------

def test_heartbeat_detects_stale_data(tmp_path):
    from datetime import datetime, timedelta
    idx = tmp_path / "index.json"
    now = datetime.now(UTC)

    idx.write_text(json.dumps({"last_updated": (now - timedelta(minutes=10)).isoformat()}))
    fresh = heartbeat.data_age_minutes(idx, now)
    assert 9 <= fresh <= 11
    assert heartbeat.is_stale(fresh) is False

    idx.write_text(json.dumps({"last_updated": (now - timedelta(days=21)).isoformat()}))
    stale = heartbeat.data_age_minutes(idx, now)
    assert heartbeat.is_stale(stale) is True


def test_heartbeat_treats_missing_or_corrupt_index_as_stale(tmp_path):
    """Absence of evidence is the outage, not a reason to stay silent."""
    assert heartbeat.data_age_minutes(tmp_path / "nope.json") is None
    assert heartbeat.is_stale(None) is True

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert heartbeat.data_age_minutes(bad) is None

    no_field = tmp_path / "nf.json"
    no_field.write_text(json.dumps({"files": {}}))
    assert heartbeat.data_age_minutes(no_field) is None


# --- backtest must fail loudly on a zero self-dimension --------------------------

def test_backtest_flags_zero_self_dimension():
    """A 0.0 dimension against the trader's own history is always a bug; None
    (excluded for thin data) is not."""
    from src.backtest import zero_dimensions
    assert zero_dimensions({"hold_duration": 0.0, "timing_profile": 0.85}) == ["hold_duration"]
    assert zero_dimensions({"hold_duration": None, "timing_profile": 0.85}) == []
    assert zero_dimensions({"a": 0.0, "b": 0.0}) == ["a", "b"]
    assert zero_dimensions({}) == []


# --- vetoed wallets must not receive the behavioural rare-market bonus -----------

def test_veto_suppresses_rare_market_bonus():
    """A style-vetoed wallet must not get the xyz: similarity boost.

    Six wallets in a 30-wallet leaderboard sample shared xyz:BRENTOIL and all were
    style-vetoed, yet the bonus lifted three to 0.57 — above the target's own
    0.5395 self-match — and tiered them CONFIRMED_CANDIDATE. The self-match
    backtest failed with margin -0.0305 as a direct result.
    """
    from src.scanner import VETO_SCORE_CAP, build_candidate_fingerprint, compute_similarity
    from tests.test_style_matching import scalper_fills, swing_fills

    eff = {"high": 0.52, "medium": 0.47, "low": 0.42}
    xyz = "xyz:BRENTOIL"
    # Same rare market on both sides, but incompatible styles -> vetoed.
    swing = build_candidate_fingerprint(swing_fills(coin=xyz), {})
    scalp = build_candidate_fingerprint(scalper_fills(coin=xyz), {})

    score, _, evidence = compute_similarity(swing, scalp, eff)
    assert evidence["vetoes"], "expected a style veto for swing vs scalper"
    assert evidence["asset_overlap"]["rare_overlap"] == [xyz]
    # Capped, not bonus-lifted above the cap.
    assert score <= VETO_SCORE_CAP, f"vetoed wallet bonus-lifted to {score}"
    # The overlap is still recorded so the lead survives on the watchlist.
    assert xyz in evidence["asset_overlap"]["overlap"]


def test_market_bonus_requires_measured_rarity_not_the_xyz_prefix():
    """The bonus is earned by measured rarity, not by a market name prefix.

    Supersedes the old flat-bonus behaviour: an xyz: market only pays out when the
    calibration data actually shows it is uncommon. With no measurement at all the
    bonus is zero, which is the conservative default.
    """
    from src import calibration as c
    from src.scanner import build_candidate_fingerprint, compute_similarity
    from tests.test_style_matching import DAY_MS, swing_fills

    eff = {"high": 0.52, "medium": 0.47, "low": 0.42}
    market = "xyz:BRENTOIL"
    a = build_candidate_fingerprint(swing_fills(coin=market), {})
    b = build_candidate_fingerprint(
        swing_fills(coin=market, start=1_700_000_000_000 + 30 * DAY_MS), {})

    def table(eligible, hits):
        return c.summarise_market_observations([{
            "recorded_at": "2026-07-27T00:00:00+00:00",
            "eligible_wallets": eligible, "market_counts": {market: hits}}])

    no_data, _, ev_none = compute_similarity(a, b, eff, table(0, 0))
    common, _, ev_common = compute_similarity(a, b, eff, table(1000, 200))   # 20%
    rare, _, ev_rare = compute_similarity(a, b, eff, table(1000, 2))         # 0.2%

    assert not ev_rare["vetoes"]
    assert ev_none["market_rarity"]["bonus_applied"] == 0.0    # unmeasured -> nothing
    assert ev_common["market_rarity"]["bonus_applied"] == 0.0  # popular -> nothing
    assert ev_rare["market_rarity"]["bonus_applied"] > 0.0     # genuinely rare -> paid
    assert rare > common == no_data


# --- heartbeat threshold must sit above the MEASURED scheduling jitter ----------

def test_stale_threshold_exceeds_observed_scheduling_jitter():
    """GitHub honours ~5% of this repo's high-frequency cron. Measured over the
    100 most recent collect runs: median gap 83 min, p90 160 min, max 220 min.
    A threshold at or below that range produces routine false alarms."""
    OBSERVED_MAX_GAP_MIN = 220
    assert heartbeat.STALE_AFTER_MINUTES > OBSERVED_MAX_GAP_MIN, (
        f"threshold {heartbeat.STALE_AFTER_MINUTES} would fire on normal jitter "
        f"(observed max gap {OBSERVED_MAX_GAP_MIN} min)")
    # Normal-but-slow intervals must NOT be reported stale.
    for gap in (46, 83, 160, 220):
        assert heartbeat.is_stale(gap) is False, f"{gap} min wrongly flagged stale"
    # A genuine multi-day stall must be.
    assert heartbeat.is_stale(24 * 60) is True


def test_collector_freshness_selfcheck_is_silent_when_fresh(monkeypatch, capsys):
    """The inline check backs up the heartbeat when its schedule is dropped."""
    from src import collector
    monkeypatch.setattr("src.heartbeat.data_age_minutes", lambda *a, **k: 30.0)
    collector.check_own_freshness()
    assert "had stalled" not in capsys.readouterr().out

    monkeypatch.setattr("src.heartbeat.data_age_minutes", lambda *a, **k: 5000.0)
    collector.check_own_freshness()
    assert "had stalled" in capsys.readouterr().out

    # No index yet (first run) must not report a stall.
    monkeypatch.setattr("src.heartbeat.data_age_minutes", lambda *a, **k: None)
    collector.check_own_freshness()
    assert "had stalled" not in capsys.readouterr().out


# --- exclusion must be genuine, not a way to dodge a hard comparison ------------

def test_hold_duration_participates_when_both_sides_have_episodes():
    """Task-4 guard: hold_duration must be EXCLUDED only when a side genuinely
    lacks completed episodes. Given enough on both sides it must produce a real
    similarity value and take part in scoring — not be quietly dropped."""
    from src.fingerprint import MIN_HOLD_EPISODES
    from src.scanner import build_candidate_fingerprint, compute_similarity

    eff = {"high": 0.52, "medium": 0.47, "low": 0.42}

    def episodes(n, hold_ms, coin="ETH"):
        out = []
        for d in range(n):
            out.extend(_episode(coin, d * 24 * HOUR, hold_ms, scale_ins=3, scale_outs=2))
        return out

    long_holder = episodes(20, 6 * HOUR)     # ~6h holds
    short_holder = episodes(20, 5 * MIN)     # ~5min holds

    a = build_candidate_fingerprint(long_holder, {})
    b = build_candidate_fingerprint(episodes(20, 6 * HOUR, coin="ETH"), {})
    c_fp = build_candidate_fingerprint(short_holder, {})

    for fp in (a, b, c_fp):
        assert fp["hold_duration"]["episode_count"] >= MIN_HOLD_EPISODES
        assert fp["hold_duration"]["sufficient_data"] is True

    # Both sides comparable -> the dimension is scored, not None.
    _, same_dims, _ = compute_similarity(a, b, eff)
    assert same_dims["hold_duration"] is not None
    assert same_dims["hold_duration"] > 0.9, "identical hold styles must score high"

    # And it genuinely discriminates rather than always returning ~1.
    _, diff_dims, _ = compute_similarity(a, c_fp, eff)
    assert diff_dims["hold_duration"] is not None
    assert diff_dims["hold_duration"] < same_dims["hold_duration"]


def test_hold_duration_excluded_only_below_the_episode_floor():
    """One side under the floor -> excluded. Both at/above -> scored."""
    from src.fingerprint import MIN_HOLD_EPISODES, compute_hold_duration
    from src.scanner import compare_hold_duration

    def fp(n):
        fills = []
        for d in range(n):
            fills.extend(_episode("ETH", d * 24 * HOUR, 2 * HOUR))
        return {"hold_duration": compute_hold_duration(fills)}

    at_floor = fp(MIN_HOLD_EPISODES)
    below = fp(MIN_HOLD_EPISODES - 1)
    assert at_floor["hold_duration"]["sufficient_data"] is True
    assert below["hold_duration"]["sufficient_data"] is False

    assert compare_hold_duration(at_floor, at_floor) is not None   # both fine
    assert compare_hold_duration(at_floor, below) is None          # one short
    assert compare_hold_duration(below, at_floor) is None


def test_timing_profile_excluded_only_when_too_few_distinct_days():
    """The same rule for timing. Measured cause: the backtest's windows held
    thousands of TWAP fills across only 4 distinct days, so the hourly histogram
    scored the trader 0.0045 against himself on a 0.14-weight dimension."""
    from src.fingerprint import MIN_TIMING_DAYS, compute_timing_profile
    from src.scanner import compare_timing_profiles

    def fills_over(days, hour):
        return [{"coin": "ETH", "time": d * 24 * HOUR + hour * HOUR, "sz": "1",
                 "px": "100", "side": "B", "dir": "Open Long", "startPosition": "0"}
                for d in range(days)]

    thin = compute_timing_profile(fills_over(MIN_TIMING_DAYS - 1, 14))
    rich = compute_timing_profile(fills_over(MIN_TIMING_DAYS + 5, 14))
    other = compute_timing_profile(fills_over(MIN_TIMING_DAYS + 5, 3))

    assert thin["sufficient_data"] is False
    assert rich["sufficient_data"] is True
    assert rich["distinct_days"] == MIN_TIMING_DAYS + 5

    # Thin on either side -> excluded rather than a confident near-zero.
    assert compare_timing_profiles(rich, thin) is None
    assert compare_timing_profiles(thin, rich) is None
    # Enough days on both sides -> scored, and it still discriminates.
    same = compare_timing_profiles(rich, rich)
    diff = compare_timing_profiles(rich, other)
    assert same is not None and same > 0.99
    assert diff is not None and diff < 0.1


def test_excluded_dimension_is_not_also_penalised():
    """An excluded dimension is unknown, not bad. Penalising None would restore
    the false negative the exclusion exists to prevent."""
    from src.scanner import build_candidate_fingerprint, compute_similarity
    from tests.test_style_matching import DAY_MS, swing_fills

    eff = {"high": 0.52, "medium": 0.47, "low": 0.42}
    a = build_candidate_fingerprint(swing_fills(), {})
    b = build_candidate_fingerprint(swing_fills(start=1_700_000_000_000 + 30 * DAY_MS), {})
    score, dims, _ = compute_similarity(a, b, eff)
    # swing_fills spans 21 days -> timing is measurable here; the guard is that a
    # None dimension never contributes a penalty.
    assert score > 0.0
    for v in dims.values():
        assert v is None or isinstance(v, (int, float))
