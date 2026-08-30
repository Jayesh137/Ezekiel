import json
import re
from pathlib import Path

import pytest

from src.chain.budget import BudgetExhausted, CallBudget

ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_spending_counts_calls():
    b = CallBudget(max_calls=3, seconds=100, clock=FakeClock())
    b.spend()
    b.spend()
    assert b.calls_used == 2
    assert b.remaining_calls() == 1
    assert b.exhausted_reason() is None


def test_exhausting_calls_raises_and_names_the_reason():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    assert not b.can_spend()
    assert b.exhausted_reason() == "call_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_deadline_exhausts_independently_of_call_count():
    clock = FakeClock()
    b = CallBudget(max_calls=100, seconds=10, clock=clock)
    b.spend()
    clock.t = 11.0
    assert not b.can_spend()
    assert b.exhausted_reason() == "time_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_calls_used_survives_exhaustion_so_partial_work_is_reportable():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    with pytest.raises(BudgetExhausted):
        b.spend()
    assert b.calls_used == 1


# --- the budgets must fit inside the job timeouts ------------------------------
#
# A job that is CANCELLED never reaches "Commit and push", so the cursors it
# advanced in the runner's working tree are discarded and the next run starts
# from block 0 again. That is the same livelock --reset was fixed for, reached
# through job cancellation instead. The steady state is fine; the risk is the
# first run after merge, when cursors are absent and every (chain, kind) starts
# at block 0 and every budget is actually consumed.

def _config():
    return json.loads((ROOT / "config.json").read_text())


def _timeout_seconds(workflow: str) -> int:
    """`timeout-minutes:` from a workflow, without needing a YAML parser
    (PyYAML is not in requirements.txt, so the suite must not depend on it)."""
    text = (ROOT / ".github" / "workflows" / workflow).read_text()
    match = re.search(r"^\s*timeout-minutes:\s*(\d+)\s*$", text, re.MULTILINE)
    assert match, f"{workflow} has no timeout-minutes"
    return int(match.group(1)) * 60


def test_the_trace_jobs_budgets_fit_inside_its_timeout():
    from src import tracer
    from src import transfer_graph as tg

    config = _config()
    budgeted = (
        config["collection"]["time_budget_seconds"]      # cluster sweep
        + tracer.TRACE_BUDGET_SECONDS                    # destination tracing
        + config["transfer_graph"]["time_budget_seconds"]  # frontier expansion
        + tg.CODE_LOOKUP_SECONDS                         # bytecode labelling
    )
    timeout = _timeout_seconds("trace.yml")
    # Headroom for checkout, setup-python, a cached pip install and the push.
    assert budgeted <= timeout - 30, (
        f"trace job budgets total {budgeted}s against a {timeout}s timeout; "
        f"a cancelled job discards the cursors it advanced")


def test_the_backfill_jobs_budget_fits_inside_its_timeout():
    config = _config()
    budgeted = config["backfill"]["time_budget_seconds"]
    timeout = _timeout_seconds("backfill.yml")
    # The job also runs `python src/backfill.py` and the commit/push.
    assert budgeted <= timeout - 600, (
        f"backfill budget {budgeted}s against a {timeout}s timeout")


def test_the_collection_budget_is_not_wider_than_the_backfill_one():
    """collection is the every-30-minutes incremental budget; backfill is the
    on-demand full-history one. Inverting them would put the long read in the
    job that cannot afford it."""
    config = _config()
    assert (config["collection"]["time_budget_seconds"]
            < config["backfill"]["time_budget_seconds"])
