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


# --- The cron gate: a cron run steps aside instead of evicting ---------------
from scripts.keep_schedule import gate  # noqa: E402


def test_cron_run_steps_aside_when_its_group_is_busy():
    # trace's 03:04 cron evicted the day's only analyze run, which was waiting.
    runs = fresh(**{"trace.yml": run(40), "analyze.yml": run(0, "queued")})
    proceed, reason = gate("trace.yml", runs, NOW)
    assert not proceed and "analyze.yml" in reason


def test_cron_run_steps_aside_when_the_keeper_already_ran_it():
    proceed, _ = gate("trace.yml", fresh(**{"trace.yml": run(10)}), NOW)
    assert not proceed


def test_cron_run_goes_ahead_when_idle_and_due():
    # The keeper is dead: the cron is the fallback and must still run.
    proceed, _ = gate("trace.yml", fresh(**{"trace.yml": run(45)}), NOW)
    assert proceed


def test_cron_gate_fails_open_on_an_unreadable_run_list():
    runs = fresh(**{"trace.yml": None, "collect.yml": None})
    assert gate("trace.yml", runs, NOW)[0]


def test_keeper_cron_steps_aside_while_a_keeper_runs():
    assert not gate("keeper.yml", {"keeper.yml": run(30, "in_progress")}, NOW)[0]
    assert gate("keeper.yml", {"keeper.yml": run(30)}, NOW)[0]


def test_watch_gate_ignores_the_busy_data_commit_group():
    runs = fresh(**{"watch.yml": run(15), "trace.yml": run(0, "in_progress")})
    assert gate("watch.yml", runs, NOW)[0]


# --- A run that did no work is not the workflow having run -------------------
from scripts import keep_schedule as ks  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _Session:
    """Answers the runs list and each run's jobs from fixed data."""

    def __init__(self, rows, jobs=None):
        self.rows, self.jobs, self.job_reads = rows, jobs or {}, 0

    def get(self, url, **_):
        if url.endswith("/jobs"):
            self.job_reads += 1
            return _Resp({"jobs": self.jobs[int(url.split("/")[-2])]})
        return _Resp({"workflow_runs": self.rows})


def _row(run_id, minutes_ago, conclusion="success", event="workflow_dispatch", status="completed"):
    stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"id": run_id, "status": status, "conclusion": conclusion, "event": event,
            "created_at": stamp}


def test_an_evicted_run_does_not_restart_the_interval():
    # 2026-09-30: analyze's only run that day was evicted at 03:04.
    s = _Session([_row(2, 60, "cancelled"), _row(1, 1500)])
    newest = ks.newest_run(s, "o/r", "analyze.yml")
    assert newest["created_at"] == NOW - timedelta(minutes=1500)


def test_a_gated_off_cron_run_does_not_restart_the_interval():
    ks._no_work_cache.clear()
    s = _Session([_row(12, 5, event="schedule"), _row(11, 40)],
                 jobs={12: [{"name": "gate", "conclusion": "success"},
                            {"name": "trace", "conclusion": "skipped"}]})
    assert ks.newest_run(s, "o/r", "trace.yml")["created_at"] == NOW - timedelta(minutes=40)
    ks.newest_run(s, "o/r", "trace.yml")
    assert s.job_reads == 1  # each run's jobs are read once


def test_a_cron_run_that_worked_counts():
    ks._no_work_cache.clear()
    s = _Session([_row(22, 5, event="schedule")],
                 jobs={22: [{"name": "gate", "conclusion": "success"},
                            {"name": "trace", "conclusion": "success"}]})
    assert ks.newest_run(s, "o/r", "trace.yml")["created_at"] == NOW - timedelta(minutes=5)


def test_a_running_run_counts_and_the_gate_excludes_its_own():
    s = _Session([_row(32, 0, status="in_progress", conclusion=None), _row(31, 50)])
    assert ks.newest_run(s, "o/r", "trace.yml")["status"] == "in_progress"
    assert ks.newest_run(s, "o/r", "trace.yml", exclude_run_id=32)["status"] == "completed"


def test_the_study_runs_every_six_hours_in_its_own_group():
    from scripts.keep_schedule import GROUP_MEMBERS
    assert {"file": "study.yml", "minutes": 360, "group": "study"} in SCHEDULE
    assert GROUP_MEMBERS["study"] == ["study.yml"]
