"""Every step's internal time budget must fit inside the timeout that kills it.

Timeouts nest, and the ORDER is the invariant (CLAUDE.md): a script's own
budget stops it cleanly and saves what it read; the step's `timeout-minutes`
kills it and saves nothing. Measured 2026-09-29/30: check_execution_program.py
read under a 600s ReadBudget inside a 4-minute step, so trace.yml failed on 34
of 36 runs and the detector never once wrote a report. Nothing checked the
two numbers against each other; this does, for every workflow step that runs
a script declaring a budget — as a CLI flag in the workflow, as an argparse
default, or as `ReadBudget(seconds=...)` with a literal or a module constant.

PyYAML is deliberately not a dependency of this suite, so the workflow text is
read the same way tests/test_workflow_step_independence.py reads it.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
# Room after the budget for the script's setup, a request in flight when the
# budget runs out, and saving: the signature load alone measured ~12s.
HEADROOM_SECONDS = 60
FLAG = r"--(?:time-)?budget-seconds|--seconds"
# Modes in which the script's declared budget does not apply at all.
UNBUDGETED_MODES = {
    "keep_schedule.py --gate": "the cron gate makes a few reads and exits; the "
                               "hour-long --budget-seconds is the keeper loop's",
    "collect_market_discovery.py --once": "--seconds bounds --stream only; --once "
                                          "is one bounded snapshot pass",
}


def _number(expr, source):
    """A literal, an arithmetic of literals, or a module-level constant."""
    expr = expr.strip()
    if re.fullmatch(r"[\d.\s*+]+", expr):
        return eval(expr)  # noqa: S307 - digits and * + only, matched above
    m = re.search(rf"^{re.escape(expr)}\s*=\s*([\d.\s*+]+)$", source, re.MULTILINE)
    return eval(m.group(1)) if m else None  # noqa: S307


def script_budget(run_line):
    """Seconds of budget a step's command runs under, or None if it declares none."""
    if any(re.search(re.escape(mode).replace(r"\ ", r"\s.*"), run_line) for mode in UNBUDGETED_MODES):
        return None
    flag = re.search(rf"(?:{FLAG})[= ]([\d.]+)", run_line)
    if flag:
        return float(flag.group(1))
    script = re.search(r"python3?\s+((?:scripts|src)/\S+\.py)", run_line)
    if not script:
        return None
    source = (ROOT / script.group(1)).read_text(encoding="utf-8")
    default = re.search(rf"add_argument\(\s*\"(?:{FLAG})\"[^)]*?default=([^,)]+)", source, re.S)
    if default and default.group(1).strip() != "None":
        return _number(default.group(1), source)
    budget = re.search(r"ReadBudget\(seconds=([^,)]+)", source)
    return _number(budget.group(1), source) if budget else None


def budgeted_steps():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        job_timeouts = [int(t) for t in re.findall(r"^    timeout-minutes:\s*(\d+)", text, re.M)]
        for body in re.split(r"^      - ", text, flags=re.MULTILINE)[1:]:
            run = re.search(r"run:\s*(.+)", body)
            if not run:
                continue
            budget = script_budget(run.group(1))
            if budget is None:
                continue
            step = re.search(r"^\s*timeout-minutes:\s*(\d+)", body, re.M)
            limit = int(step.group(1)) if step else max(job_timeouts, default=360)
            yield pytest.param(budget, limit, id=f"{path.name}:{run.group(1)[:60]}")


@pytest.mark.parametrize(("budget", "limit_minutes"), list(budgeted_steps()))
def test_the_budget_ends_before_the_timeout_kills_the_step(budget, limit_minutes):
    assert budget + HEADROOM_SECONDS <= limit_minutes * 60, (
        f"a {budget:.0f}s budget inside a {limit_minutes}-minute timeout: the step is killed "
        f"before the script can stop itself and save — raise timeout-minutes or cut the budget")


def test_the_check_finds_the_steps_it_exists_for():
    ids = [p.id for p in budgeted_steps()]
    assert any("check_execution_program" in i for i in ids)
    assert any("census_execution_program" in i for i in ids)
    assert any("collect_tape" in i for i in ids)
