"""The schedule keeper: the dispatcher's decisions, made on GitHub itself.

Measured 2026-09-28: collect ran at 06:58 and next at 15:17 UTC, the heartbeat
failed twice on 475 minutes of stale data, and nothing had dispatched a single
run from 03:45 to 20:50 because the only dispatcher was a PC that was asleep.
These pin the rules the keeper inherits from scripts/dispatch_workflows.ps1.
"""

from datetime import UTC, datetime, timedelta

from scripts.keep_schedule import SCHEDULE, plan

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def run(minutes_ago, status="completed"):
    return {"status": status, "created_at": NOW - timedelta(minutes=minutes_ago)}


def fresh(**overrides):
    """Every scheduled workflow just ran and finished; override per workflow."""
    runs = {job["file"]: run(1) for job in SCHEDULE}
    runs.update({"backfill.yml": run(9999), "substrate-backfill.yml": run(9999)})
    runs.update(overrides)
    return runs


def test_nothing_due_dispatches_nothing_and_says_when_to_wake():
    result = plan(fresh(), NOW)
    assert result.dispatch == []
    assert not result.in_progress
    # watch.yml (10 min) ran 1 min ago, so it is the first due, in 9 minutes.
    assert result.next_due_seconds == 9 * 60


def test_overdue_workflow_is_dispatched():
    result = plan(fresh(**{"collect.yml": run(20)}), NOW)
    assert result.dispatch == ["collect.yml"]


def test_one_dispatch_per_group_most_overdue_first():
    # collect 2x overdue (30/15), trace 1.33x (40/30): only collect goes, because a
    # second dispatch into the group would queue and be evicted by the third.
    result = plan(fresh(**{"collect.yml": run(30), "trace.yml": run(40)}), NOW)
    assert result.dispatch == ["collect.yml"]


def test_groups_are_independent():
    result = plan(fresh(**{"watch.yml": run(11), "collect.yml": run(16)}), NOW)
    assert sorted(result.dispatch) == ["collect.yml", "watch.yml"]


def test_never_queues_behind_a_busy_group():
    # A backfill the keeper never dispatches still occupies data-commit.
    runs = fresh(**{"collect.yml": run(30), "substrate-backfill.yml": run(5, "in_progress")})
    result = plan(runs, NOW)
    assert result.dispatch == []
    assert result.in_progress


def test_workflow_still_running_is_not_redispatched():
    result = plan(fresh(**{"watch.yml": run(30, "queued")}), NOW)
    assert "watch.yml" not in result.dispatch
    assert result.in_progress


def test_unreadable_run_list_is_not_dispatched_blind():
    runs = fresh(**{"collect.yml": run(45)})
    runs["watch.yml"] = None
    result = plan(runs, NOW)
    # watch is unknown, so it is not dispatched; the other group is unaffected.
    assert result.dispatch == ["collect.yml"]
    assert "watch.yml" in result.unreadable


def test_unreadable_group_member_counts_as_busy():
    # If we cannot tell whether a backfill is running, dispatching into its group
    # could queue behind it and be evicted: treat unknown as busy.
    runs = fresh(**{"collect.yml": run(30), "backfill.yml": None})
    assert plan(runs, NOW).dispatch == []


def test_never_run_workflow_is_due():
    runs = fresh()
    runs["scan.yml"] = "never"
    assert plan(runs, NOW).dispatch == ["scan.yml"]


def test_intervals_match_the_pc_dispatcher():
    import re

    text = open("scripts/dispatch_workflows.ps1", encoding="utf-8").read()
    ps1 = {f: int(m) for f, m in re.findall(r'File = "([\w.-]+)";\s*Minutes = (\d+)', text)}
    assert ps1 == {job["file"]: job["minutes"] for job in SCHEDULE}


def test_keeper_is_triggered_by_every_workflow_it_waits_on():
    # The keeper exits while something runs and relies on that run's completion to
    # wake it. A workflow missing from workflow_run would strand the schedule.
    import re

    text = open(".github/workflows/keeper.yml", encoding="utf-8").read()
    block = re.search(r"workflows:\s*\[(.*?)\]", text, re.S).group(1)
    listed = set(re.findall(r"'([^']+)'", block))
    names = {}
    for wf in [job["file"] for job in SCHEDULE] + ["backfill.yml", "substrate-backfill.yml"]:
        body = open(f".github/workflows/{wf}", encoding="utf-8").read()
        names[wf] = re.search(r"^name:\s*(.+)$", body, re.M).group(1).strip().strip("'\"")
    missing = {wf: n for wf, n in names.items() if n not in listed}
    assert not missing, missing
