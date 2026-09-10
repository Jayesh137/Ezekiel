# tests/test_dormancy.py
"""One wallet goes quiet, another starts — the migration signature needing no link.

Every other vector requires a connection: a transfer, a shared deposit address,
an authorised agent, a matching amount. This one requires none, which is exactly
why it matters. If he funds a fresh wallet from somewhere unobservable, the
alignment of his silence with its birth is all that is left.

Everything is judged against HIS OWN rhythm. Measured 2026-09-10 across 75 active
days: median gap 2, p90 5, p95 10, longest ever 21. A fixed "dormant after a
week" rule would fire constantly on a trader like that.
"""

from src.dormancy import (
    active_days,
    anomalous_gaps,
    dormancy_state,
    gap_stats,
    handoff_score,
)

DAY = 86_400_000


def fills_on(days):
    return [{"time": d * DAY + 1} for d in days]


def test_active_days_are_whole_days_and_deduplicated():
    assert active_days([{"time": 5 * DAY}, {"time": 5 * DAY + 999}, {"time": 7 * DAY}]) \
        == [5, 7]


def test_junk_fills_are_ignored():
    assert active_days([{"time": None}, {}, "nope", None]) == []


def test_gap_stats_describe_his_rhythm():
    stats = gap_stats([0, 2, 4, 6, 30])
    assert stats["active_days"] == 5
    assert stats["median"] == 2
    assert stats["max"] == 24


def test_a_normal_pause_is_not_dormancy():
    """He is quiet for a day or two constantly. Calling that dormant would make
    the alert worthless within a month."""
    days = list(range(0, 100, 2))
    state = dormancy_state(days, now_day=days[-1] + 2)
    assert state["unusual"] is False
    assert state["unprecedented"] is False


def test_a_silence_beyond_his_p90_is_unusual():
    """Longer than he usually pauses, but he has been quieter than this before,
    so it is notable rather than a change of behaviour."""
    days = [*range(0, 100, 2), 130]        # he has taken 30 days off before
    state = dormancy_state(days, now_day=130 + 10)
    assert state["unusual"] is True
    assert state["unprecedented"] is False


def test_a_silence_longer_than_any_he_has_taken_is_unprecedented():
    """The one that matters: not a quiet week but a change of behaviour."""
    days = [0, 2, 4, 6, 8, 30]           # longest historical gap is 22
    state = dormancy_state(days, now_day=30 + 40)
    assert state["unprecedented"] is True


def test_no_history_cannot_be_judged_dormant():
    """Absence of data is not evidence of silence."""
    state = dormancy_state([], now_day=100)
    assert state["silent_days"] is None
    assert state["unusual"] is False
    assert "cannot judge" in state["reason"]


def test_only_unusually_long_gaps_count_as_anomalous():
    days = [0, 2, 4, 6, 8, 10, 12, 40]
    gaps = anomalous_gaps(days)
    assert [g["length"] for g in gaps] == [28]


def test_a_wallet_born_inside_a_silence_scores():
    """The finding this module exists for."""
    target = [0, 2, 4, 6, 8, 10, 12, 40]      # a 28-day silence opens at day 12
    got = handoff_score(target, active_days(fills_on([14, 16, 18])))
    assert got["score"] > 0
    assert got["gap_length"] == 28
    assert got["delay_days"] == 2


def test_a_wallet_already_trading_before_the_silence_is_not_a_handoff():
    """Otherwise half the leaderboard qualifies — they all exist during his
    quiet spells."""
    target = [0, 2, 4, 6, 8, 10, 12, 40]
    got = handoff_score(target, active_days(fills_on([1, 3, 14, 16])))
    assert got["score"] == 0.0


def test_a_wallet_starting_long_after_the_silence_opens_is_not_a_handoff():
    target = [0, 2, 4, 6, 8, 10, 12, 40]
    got = handoff_score(target, active_days(fills_on([30, 32])))
    assert got["score"] == 0.0


def test_starting_during_an_ordinary_gap_scores_nothing():
    """He is quiet constantly; only an ANOMALOUS silence is meaningful."""
    target = list(range(0, 100, 2))
    got = handoff_score(target, active_days(fills_on([51, 53])))
    assert got["score"] == 0.0


def test_a_longer_silence_makes_a_stronger_handoff():
    short = [0, 2, 4, 6, 8, 10, 12, 24]
    long = [0, 2, 4, 6, 8, 10, 12, 60]
    cand = active_days(fills_on([13, 15]))
    assert handoff_score(long, cand)["score"] > handoff_score(short, cand)["score"]


def test_a_tighter_start_makes_a_stronger_handoff():
    target = [0, 2, 4, 6, 8, 10, 12, 40]
    near = handoff_score(target, active_days(fills_on([13])))
    far = handoff_score(target, active_days(fills_on([15])))
    assert near["score"] > far["score"]


def test_missing_activity_on_either_side_scores_nothing():
    assert handoff_score([], [1, 2])["score"] == 0.0
    assert handoff_score([1, 2], [])["score"] == 0.0
