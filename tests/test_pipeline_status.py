"""A failing workflow must keep saying so, and a recovered one must re-arm.

trace.yml failed 34 runs in a row on 2026-09-29/30 behind an issue opened once
(2026-09-27) and never pinged again.
"""

from datetime import UTC, datetime, timedelta

from scripts.pipeline_status import REPING_HOURS, decide

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
ISSUE = {"number": 35}


def test_first_failure_opens_and_pings():
    assert decide("failure", None, None, NOW) == "open"


def test_a_failure_inside_the_quiet_window_stays_quiet():
    assert decide("failure", ISSUE, NOW - timedelta(hours=1), NOW) == "quiet"


def test_a_pipeline_that_stays_red_pings_again():
    assert decide("failure", ISSUE, NOW - timedelta(hours=REPING_HOURS), NOW) == "reping"


def test_an_old_issue_that_never_pinged_pings_at_once():
    assert decide("failure", ISSUE, None, NOW) == "reping"


def test_recovery_closes_the_issue_and_nothing_else():
    assert decide("success", ISSUE, None, NOW) == "close"
    assert decide("success", None, None, NOW) == "none"
