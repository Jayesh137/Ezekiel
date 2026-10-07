"""A failing workflow must keep saying so, and a recovered one must re-arm.

trace.yml failed 34 runs in a row on 2026-09-29/30 behind an issue opened once
(2026-09-27) and never pinged again.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.pipeline_status import REPING_HOURS, decide

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
ISSUE = {"number": 35}
WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
# An assignment from the secret, not a mention of it: a comment cannot satisfy this.
NTFY_FROM_SECRET = re.compile(
    r"^ +NTFY_TOPIC:\s*\$\{\{\s*secrets\.NTFY_TOPIC\s*\}\}\s*$", re.MULTILINE)


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


def _jobs(text):
    """{job name: its text}, split on the two-space keys under `jobs:`."""
    parts = re.split(r"^  ([A-Za-z0-9_-]+):[ \t]*$", text.partition("\njobs:\n")[2],
                     flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _reporters():
    """{"workflow:job": whether NTFY_TOPIC reaches it} for every job that runs
    pipeline_status.py - set in that job, or for the whole workflow."""
    found = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        whole_workflow = NTFY_FROM_SECRET.search(text.partition("\njobs:\n")[0]) is not None
        for job, body in _jobs(text).items():
            if "pipeline_status.py" in body:
                found[f"{path.name}:{job}"] = (
                    whole_workflow or NTFY_FROM_SECRET.search(body) is not None)
    return found


def test_every_workflow_that_reports_a_failure_can_reach_the_phone():
    """pipeline_status.py pushes to ntfy only when NTFY_TOPIC is in its
    environment (it prints "NTFY_TOPIC unset - issue only" otherwise). study.yml
    shipped without it, so a dead study would have opened a GitHub issue and
    nothing else - and nothing else watches the study yet. An issue is not a
    channel anyone watches (see the module docstring)."""
    reporters = _reporters()
    assert {"study.yml:study", "trace.yml:trace"} <= set(reporters), (
        f"the guard no longer finds the jobs it exists for: {sorted(reporters)}")
    silent = sorted(job for job, reaches in reporters.items() if not reaches)
    assert not silent, (
        f"these jobs run pipeline_status.py without NTFY_TOPIC in their env, so a "
        f"failure opens an issue and never reaches the phone: {silent}")
