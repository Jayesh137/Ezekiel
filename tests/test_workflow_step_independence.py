"""One failing step must cost its own reading, not every step behind it.

A workflow step with no `if:` carries an implicit `success()`, so a single
failure skips the whole remainder of the job. Measured 2026-09-16: the graph
step timed out on 4 of trace.yml's last 10 runs, and each failure ALSO silently
skipped the six detectors that follow it — identities, agents, dormancy,
portfolio overlap, the roster and the accounting. The workflows already carried
the right reasoning for the commit step ("a step that failed costs its own
reading, not the whole run's") and it had never been extended to the detectors
it was written about.

That is the stalled-frontier failure shape again: an absence that looks like
nothing happening. Nothing in a run summary says "six detectors did not run" —
the job simply goes red for the one step that failed.

`!cancelled()` rather than `always()`, because on CANCELLATION the runner is
tearing down and the remaining seconds belong to "Commit and push" (which keeps
`always()`, since persisting what exists is worth doing in every state). Gated
on `steps.deps` because a failed `pip install` is a broken environment, not an
independent sibling, and sixteen ImportErrors is noise rather than resilience.

PyYAML is deliberately not a dependency of this suite (see
tests/test_chain_budget.py), so these read the workflow text the same way.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
WORKFLOWS = ("trace.yml", "scan.yml", "analyze.yml", "watch.yml")

# Steps whose whole job is to run after a failure, or to report one.
TERMINAL = {"Commit and push": "always()", "Open failure issue": "failure()"}


def _steps(workflow: str):
    """(name, body) per step, split on the step-list indent."""
    text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
    # Steps start at exactly six spaces + "- "; anything deeper belongs to one.
    parts = re.split(r"^      - ", text, flags=re.MULTILINE)[1:]
    out = []
    for body in parts:
        body = "      - " + body
        m = re.search(r"^\s*(?:-\s*)?name:\s*(.+?)\s*$", body, re.MULTILINE)
        run = re.search(r"^\s*(?:-\s*)?run:\s*(.+?)\s*$", body, re.MULTILINE)
        name = m.group(1) if m else (run.group(1) if run else "<uses>")
        out.append((name.strip("'\""), body))
    return out


def _runs_python(body: str) -> bool:
    return re.search(r"^\s*(?:-\s*)?run:.*\bpython\b", body, re.MULTILINE) is not None


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_every_detector_survives_a_sibling_step_failing(workflow):
    unguarded = []
    for name, body in _steps(workflow):
        if name in TERMINAL or not _runs_python(body):
            continue
        if "!cancelled()" not in body:
            unguarded.append(name)
    assert not unguarded, (
        f"{workflow}: these steps still carry the implicit success() gate, so "
        f"an earlier failure skips them entirely: {unguarded}"
    )


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_the_environment_is_still_a_gate(workflow):
    """Independent of SIBLINGS is not the same as unconditional. A failed
    `pip install` means no interpreter has the dependencies, and running the
    detectors then produces a wall of ImportErrors rather than any reading."""
    assert re.search(r"^      - id: deps$", (ROOT / ".github" / "workflows" / workflow)
                     .read_text(encoding="utf-8"), re.MULTILINE), \
        f"{workflow}: the dependency step needs `id: deps` for the guards to name it"
    for name, body in _steps(workflow):
        if name in TERMINAL or not _runs_python(body):
            continue
        assert "steps.deps.conclusion == 'success'" in body, (
            f"{workflow}: step {name!r} runs even when pip install failed")


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_the_commit_and_failure_steps_keep_their_own_conditions(workflow):
    """The fix must not disturb the two steps that were already right:
    the push persists whatever completed, and the issue still gets opened."""
    found = {name: body for name, body in _steps(workflow) if name in TERMINAL}
    for name, expected in TERMINAL.items():
        assert name in found, f"{workflow} lost its {name!r} step"
        assert re.search(rf"^\s*if:\s*{re.escape(expected)}\s*$",
                         found[name], re.MULTILINE), \
            f"{workflow}: {name!r} must stay `if: {expected}`"


def test_every_committing_workflow_persists_what_it_finished():
    """A run that did the work and could not save it did the work for nothing.

    `backfill.yml` and `substrate-backfill.yml` were the two that did not have
    this — and they are the LONGEST jobs in the repo (48-60 minutes) and the
    ones that advance sweep cursors. A step failure fifty minutes in discarded
    every cursor the sweep had advanced, so the next run restarted from block
    0: the livelock `--reset` was fixed for, reached through a step failure
    rather than the cancellation the existing comments reason about.
    """
    missing = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        steps = dict(_steps(path.name))
        body = steps.get("Commit and push")
        if body is None:
            continue
        if not re.search(r"^\s*if:\s*always\(\)\s*$", body, re.MULTILINE):
            missing.append(path.name)
    assert not missing, (
        f"these workflows commit data but drop it when an earlier step fails: "
        f"{missing}")


def test_ci_reports_lint_and_tests_independently():
    """The suite's own CI had the cascade this file is about, and it bit twice.

    `Lint` ran before `Tests` with no condition, so on 2026-09-16 a single
    unused import failed Lint and `Tests` was SKIPPED by the implicit
    `success()` — two consecutive pushes went red without the 1362-test suite
    ever having run in CI. "Fail fast" saves about three minutes of runner
    time and costs the answer to the only question CI is asked.

    Both still fail the job, so nothing is hidden; they simply both report.
    Same for the dashboard's unit tests and its build, which are two separate
    answers about the same code.
    """
    text = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    steps = dict(_steps("test.yml"))
    for name in ("Lint", "Tests", "Unit tests", "Build"):
        assert name in steps, f"test.yml lost its {name!r} step"
        assert "!cancelled()" in steps[name], (
            f"test.yml: {name!r} is skipped when a sibling step fails, so a "
            f"trivial failure hides the result it exists to report")
    assert re.search(r"^      - id: deps$", text, re.MULTILINE)
    assert re.search(r"^        id: npm$", text, re.MULTILINE)


def test_a_failing_step_still_fails_the_run():
    """`!cancelled()` changes which steps RUN, never whether the job goes red —
    a step that runs and fails still fails the job. Pinned by the fact that no
    step carries `continue-on-error`, which is the thing that WOULD hide it."""
    for workflow in WORKFLOWS:
        text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
        assert "continue-on-error" not in text, (
            f"{workflow}: continue-on-error would make a failed detector "
            f"invisible — the run would go green with a reading missing")


def test_repricing_stays_gated_on_the_quarantine_that_protects_it():
    """THE exception, and the reason each condition was reasoned about.

    `reprice_stored_records` prices by SYMBOL, and its only defence against a
    counterfeit is `_needs_price`'s `not rec.get("spam")` — the flag the
    quarantine step sets. Running it after a FAILED quarantine looks up impostor
    tokens by ticker and books them as real money: rule 2, which once booked
    $3.07B of counterfeit value, reached this time through a CI condition.

    Every other step in analyze.yml loses only freshness when a predecessor
    fails. This one loses correctness, so it keeps the gate the implicit
    `success()` used to give it for free.
    """
    steps = dict(_steps("analyze.yml"))
    assert "Quarantine impostor tokens" in steps
    assert re.search(r"^\s*id: quarantine$", steps["Quarantine impostor tokens"],
                     re.MULTILINE), "the quarantine step must keep `id: quarantine`"
    body = steps["Reprice stored transfers"]
    assert "steps.quarantine.conclusion == 'success'" in body, (
        "Reprice stored transfers must not run after a failed quarantine: it "
        "prices by symbol and would book counterfeit tokens as real money")
