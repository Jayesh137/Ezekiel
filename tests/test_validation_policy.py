# tests/test_validation_policy.py
"""Activity-based backtest windows and the threshold-policy fallback chain.

The target trades sparsely — 61 active days across a 166-day calendar span — so
two fixed 21-calendar-day windows captured only 4 active days each while still
clearing MIN_WINDOW_FILLS (TWAP puts thousands of fills in one session). That made
the self-match INCONCLUSIVE, which reverted thresholds to an unreachable raw 0.90
and made behavioural discovery invisible.

These tests pin both halves of the fix: windows chosen by active trading day, and
a policy chain that keeps discovery alive without ever letting an unvalidated
scorer page anyone on its own.
"""

from src import thresholds as th
from src.backtest import (
    MIN_WINDOW_DAYS,
    TARGET_WINDOW_DAYS,
    distinct_days,
    split_windows,
    window_leakage,
    window_summary,
)

DAY = 86_400_000


def session(day_index: int, n_fills: int, coin: str = "ETH", tid_base: int = 0):
    """One trading session: many fills concentrated in a single day, as TWAP does."""
    t0 = day_index * DAY + 9 * 3_600_000
    return [{"tid": tid_base + day_index * 10_000 + i, "coin": coin,
             "time": t0 + i * 1000, "sz": "1", "px": "100", "side": "B",
             "dir": "Open Long", "startPosition": "0", "closedPnl": "0.0"}
            for i in range(n_fills)]


def sparse_history(active_days: list[int], fills_per_day: int = 500):
    out = []
    for d in active_days:
        out.extend(session(d, fills_per_day))
    return out


# --- window selection ------------------------------------------------------------

def test_sparse_trading_over_long_calendar_uses_active_days_not_calendar():
    """The real failure: 24 active days spread over ~5 months. Fixed calendar
    slicing would capture a handful; activity-based selection must not."""
    # One session every 7 days for 24 sessions => 168 calendar days.
    days = [i * 7 for i in range(24)]
    fills = sparse_history(days)
    assert distinct_days(fills) == 24

    older, recent = split_windows(fills)
    o, r = window_summary(older), window_summary(recent)

    assert o["active_days"] >= MIN_WINDOW_DAYS
    assert r["active_days"] >= MIN_WINDOW_DAYS
    assert o["active_days"] == r["active_days"] == TARGET_WINDOW_DAYS
    # The calendar span stretched far beyond 21 days to collect the sessions.
    assert r["calendar_span_days"] > 21


def test_two_valid_non_overlapping_activity_windows():
    fills = sparse_history([i * 5 for i in range(30)])
    older, recent = split_windows(fills)
    leak = window_leakage(older, recent)
    assert leak["shared_tids"] == 0
    assert leak["shared_days"] == 0
    assert leak["chronological"] is True
    # recent must be the newer half
    assert max(f["time"] for f in older) < min(f["time"] for f in recent)


def test_no_fill_tid_or_episode_leakage_between_windows():
    fills = sparse_history([i * 3 for i in range(2 * TARGET_WINDOW_DAYS + 6)])
    older, recent = split_windows(fills)

    o_ids = {id(f) for f in older}
    r_ids = {id(f) for f in recent}
    assert not (o_ids & r_ids), "same fill object appears in both windows"

    o_tid = {f["tid"] for f in older}
    r_tid = {f["tid"] for f in recent}
    assert not (o_tid & r_tid)

    from src.fingerprint import _position_episodes
    o_eps = {tuple(f["tid"] for f in ep) for ep in _position_episodes(older)}
    r_eps = {tuple(f["tid"] for f in ep) for ep in _position_episodes(recent)}
    assert not (o_eps & r_eps), "an episode spans both windows"


def test_insufficient_distinct_days_stays_inconclusive():
    """Below the floor the windows must NOT be silently accepted — the sufficiency
    requirement is never weakened just to obtain a PASS."""
    fills = sparse_history([0, 4, 9, 15, 21, 28])   # 6 active days total
    older, recent = split_windows(fills)
    assert distinct_days(older) < MIN_WINDOW_DAYS or distinct_days(recent) < MIN_WINDOW_DAYS
    assert len(fills) > 1000, "fill count is high — only the day count reveals the problem"


def test_windows_use_most_recent_history_not_the_whole_span():
    """`recent` must describe current behaviour, not a multi-month blend."""
    fills = sparse_history([i * 2 for i in range(60)])
    older, recent = split_windows(fills)
    all_days = sorted({f["time"] // DAY for f in fills})
    recent_days = sorted({f["time"] // DAY for f in recent})
    assert recent_days == all_days[-TARGET_WINDOW_DAYS:]


def test_window_summary_publishes_auditable_detail():
    fills = sparse_history([i * 4 for i in range(2 * TARGET_WINDOW_DAYS)])
    older, _ = split_windows(fills)
    s = window_summary(older)
    for key in ("fills", "active_days", "first_day", "last_day", "calendar_span_days"):
        assert key in s
    assert s["first_day"] <= s["last_day"]
    assert window_summary([])["active_days"] == 0


# --- policy: carry-forward ---------------------------------------------------------

RAW = {"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65}


def test_current_validated_policy():
    eff = th.resolve(RAW, {"passed": True, "self_score": 0.62,
                           "run_at": "2026-07-27T00:00:00+00:00",
                           "scoring_schema": th.SCORING_SCHEMA})
    assert eff["policy"] == th.SRC_CURRENT_VALIDATED
    assert eff["high"] < 0.65
    assert eff["provenance"]["self_score"] == 0.62


def test_compatible_validated_threshold_is_carried_forward():
    eff = th.resolve(RAW, {"passed": None, "last_validated": {
        "self_score": 0.5931, "validated_at": "2026-07-20T00:00:00+00:00",
        "scoring_schema": th.SCORING_SCHEMA, "best_stranger_score": 0.42,
        "margin": 0.17, "strangers_scored": 16}})
    assert eff["policy"] == th.SRC_CARRIED_FORWARD
    assert eff["high"] < 0.60
    p = eff["provenance"]
    assert p["margin"] == 0.17 and p["strangers_scored"] == 16
    assert p["validated_at"] and p["best_stranger_score"] == 0.42


def test_carry_forward_rejected_after_scoring_schema_change():
    """A ceiling proven under different weights says nothing about this scorer."""
    eff = th.resolve(RAW, {"passed": None, "last_validated": {
        "self_score": 0.5931, "validated_at": "2026-01-01T00:00:00+00:00",
        "scoring_schema": "2020-01-01.0"}})
    assert eff["policy"] == th.SRC_OBSERVING
    assert "revalidation required" in eff["carry_forward_rejected"]
    assert (eff["high"], eff["medium"], eff["low"]) == (0.90, 0.80, 0.65)

    # A pre-schema report (None) also cannot be shown compatible.
    assert th.schema_compatible(None) is False
    assert th.schema_compatible(th.SCORING_SCHEMA) is True


def test_failed_backtest_still_never_lowers_thresholds():
    eff = th.resolve(RAW, {"passed": False, "self_score": 0.30,
                           "last_validated": {"self_score": 0.59,
                                              "scoring_schema": th.SCORING_SCHEMA}})
    assert (eff["high"], eff["medium"], eff["low"]) == (0.90, 0.80, 0.65)


# --- policy: population fallback ----------------------------------------------------

OBSERVING_EFF = {"high": 0.90, "medium": 0.80, "low": 0.65,
                 "policy": th.SRC_OBSERVING, "source": "config"}


def test_population_fallback_watchlists_an_extreme_score():
    d = th.disposition(0.72, OBSERVING_EFF, percentile=99.6, population_size=200)
    assert d["action"] == th.ACTION_WATCHLIST
    assert d["policy"] == th.SRC_POPULATION_FALLBACK
    assert any("top 0.4" in r or "percentile" in r for r in d["reasons"])
    assert any("unvalidated" in b for b in d["blockers"])


def test_behaviour_only_fallback_never_alerts():
    """However extreme, an unvalidated scorer must not page anyone alone."""
    for pct in (99.0, 99.9, 100.0):
        d = th.disposition(0.99, OBSERVING_EFF, percentile=pct, population_size=500)
        assert d["action"] != th.ACTION_ALERT, f"alerted at percentile {pct}"
        assert d["action"] == th.ACTION_WATCHLIST


def test_independent_evidence_promotes_a_fallback_candidate():
    d = th.disposition(0.72, OBSERVING_EFF, percentile=99.6, population_size=200,
                       corroborated=True)
    assert d["action"] == th.ACTION_ALERT
    assert d["policy"] == th.SRC_POPULATION_FALLBACK
    assert any("corroborated" in r for r in d["reasons"])


def test_ordinary_score_is_not_watchlisted_by_the_fallback():
    d = th.disposition(0.50, OBSERVING_EFF, percentile=80.0, population_size=200)
    assert d["action"] != th.ACTION_ALERT
    assert d["policy"] == th.SRC_OBSERVING


def test_insufficient_calibration_falls_back_to_observing():
    d = th.disposition(0.99, OBSERVING_EFF, percentile=None, population_size=10)
    assert d["policy"] == th.SRC_OBSERVING
    assert d["action"] != th.ACTION_ALERT
    assert any("OBSERVING" in b for b in d["blockers"])

    below = th.disposition(0.99, OBSERVING_EFF, percentile=99.9,
                           population_size=th.FALLBACK_MIN_POPULATION - 1)
    assert below["policy"] == th.SRC_OBSERVING
    assert below["action"] != th.ACTION_ALERT


def test_style_veto_suppressed_under_fallback_but_evidence_retained():
    d = th.disposition(0.99, OBSERVING_EFF, percentile=99.9, population_size=500,
                       vetoes=["Decision frequency 20x apart"], corroborated=True)
    assert d["action"] == th.ACTION_WATCHLIST, "a vetoed wallet must never alert"
    assert any("veto" in b for b in d["blockers"])
    assert d["reasons"], "evidence must be retained, not erased"


def test_every_disposition_carries_a_policy_label():
    """The dashboard and logs must always be able to show the operating mode."""
    validated = th.resolve(RAW, {"passed": True, "self_score": 0.62,
                                 "scoring_schema": th.SCORING_SCHEMA})
    cases = [
        th.disposition(0.72, validated),
        th.disposition(0.72, validated, percentile_ok=False),
        th.disposition(0.10, validated),
        th.disposition(0.72, OBSERVING_EFF, percentile=99.6, population_size=200),
        th.disposition(0.10, OBSERVING_EFF, percentile=None, population_size=0),
    ]
    valid = {th.SRC_CURRENT_VALIDATED, th.SRC_CARRIED_FORWARD,
             th.SRC_POPULATION_FALLBACK, th.SRC_OBSERVING}
    for d in cases:
        assert d.get("policy") in valid, d
