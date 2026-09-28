"""The schedule keeper: the dispatcher's decisions, made on GitHub itself.

Measured 2026-09-28: collect ran at 06:58 and next at 15:17 UTC, the heartbeat
failed twice on 475 minutes of stale data, and nothing had dispatched a single
run from 03:45 to 20:50 because the only dispatcher was a PC that was asleep.
These pin the rules the keeper inherits from scripts/dispatch_workflows.ps1.
"""

from datetime import UTC, datetime, timedelta

from scripts.keep_schedule import MAX_SLEEP_SECONDS, POLL_SECONDS, SCHEDULE, next_sleep, plan

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


def test_polls_while_a_run_is_in_flight():
    # Nothing announces a GITHUB_TOKEN-started run finishing, so the keeper polls.
    busy = plan(fresh(**{"collect.yml": run(2, "in_progress")}), NOW)
    assert next_sleep(busy) == POLL_SECONDS


def test_sleeps_until_next_due_when_idle_but_bounded():
    idle = plan(fresh(), NOW)  # watch due in 9 minutes
    assert next_sleep(idle) == MAX_SLEEP_SECONDS
    almost = plan(fresh(**{"watch.yml": run(9)}), NOW)  # due in 60s
    assert next_sleep(almost) == 65


def _keeper_yml():
    return open(".github/workflows/keeper.yml", encoding="utf-8").read()


def test_keeper_re_dispatches_itself_even_after_a_failure():
    # The chain is the keeper dispatching its successor. workflow_run cannot carry
    # it: a GITHUB_TOKEN-started run raises no workflow_run (measured 2026-09-28).
    import re

    text = _keeper_yml()
    step = text[text.index("- name: Re-dispatch the keeper"):]
    assert re.search(r"if:\s*always\(\)", step)
    assert "gh workflow run keeper.yml" in step
    assert "workflow_run" not in text.split("jobs:")[0].split("on:")[1]


def test_keeper_never_cancels_its_own_successor_creator():
    import re

    assert re.search(r"cancel-in-progress:\s*false", _keeper_yml())
