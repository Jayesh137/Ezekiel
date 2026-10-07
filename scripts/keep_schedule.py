"""Keep the workflows on schedule from inside GitHub Actions.

Measured 2026-09-28: collect ran at 06:58 and next at 15:17 UTC, and the
heartbeat failed twice on 475 minutes of stale data. Nothing had dispatched a
run from 03:45 to 20:50: the only dispatcher was scripts/dispatch_workflows.ps1
on a PC that was asleep, the Apps Script relay was never installed, and
GitHub's cron here arrives a median of 198 minutes apart. The schedule depended
on a machine that sleeps.

This runs the same decisions as that dispatcher, from .github/workflows/
keeper.yml, as a loop that ticks for about an hour and then dispatches the
keeper again. A GITHUB_TOKEN may trigger `workflow_dispatch` -- the one event
GitHub exempts from its no-recursion rule -- so the chain needs no token of
ours and no machine of ours.

It is a loop and not event-driven because the event-driven version was built
first and died after one link, measured 2026-09-28: a run STARTED by
GITHUB_TOKEN raises no `workflow_run` on completion, so the watch run the
keeper dispatched at 21:13 finished and nothing woke the keeper again.

Rules inherited from the PC dispatcher, each paid for once already:
  * never dispatch a workflow whose newest run is queued or running;
  * at most ONE dispatch per concurrency group per tick, most overdue first --
    a group holds one pending run, so a second dispatch evicts the first
    (trace run 35180941359, 2026-09-17);
  * never dispatch into a busy group, including runs of workflows this never
    dispatches (the backfills);
  * a run list we could not read is never dispatched blind, and an unreadable
    member of a group counts as busy.

Tick policy: when a run is in flight, look again in POLL_SECONDS; when nothing
is, sleep until the next workflow falls due (never longer than
MAX_SLEEP_SECONDS). Each tick is ~8 API calls, well inside GITHUB_TOKEN's
1,000 requests an hour. keeper.yml re-dispatches the keeper in an `always()`
step, so a crash re-arms it too; its hourly cron restarts a broken chain.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import requests

# Must agree with scripts/dispatch_workflows.ps1 (pinned by a test) so the two
# schedulers never double up: each dispatches only when the newest run is
# finished and older than its interval, whoever started it.
SCHEDULE = [
    {"file": "watch.yml", "minutes": 10, "group": "watch"},
    {"file": "tape.yml", "minutes": 20, "group": "tape"},
    {"file": "collect.yml", "minutes": 15, "group": "data-commit"},
    {"file": "trace.yml", "minutes": 30, "group": "data-commit"},
    {"file": "scan.yml", "minutes": 60, "group": "data-commit"},
    {"file": "analyze.yml", "minutes": 1440, "group": "data-commit"},
    {"file": "study.yml", "minutes": 360, "group": "study"},
]

# Every workflow in each concurrency group, including ones never dispatched here.
GROUP_MEMBERS = {
    "watch": ["watch.yml"],
    "tape": ["tape.yml"],
    "data-commit": [
        "collect.yml", "trace.yml", "scan.yml", "analyze.yml",
        "backfill.yml", "substrate-backfill.yml",
    ],
    "study": ["study.yml"],
}

# The keeper's own group, for the cron gate only: tick() never dispatches it.
GATE_GROUPS = {**GROUP_MEMBERS, "schedule-keeper": ["keeper.yml"]}

NEVER = "never"  # a workflow with no runs at all: due immediately
POLL_SECONDS = 90
MAX_SLEEP_SECONDS = 5 * 60


@dataclass
class Plan:
    dispatch: list[str] = field(default_factory=list)
    in_progress: bool = False
    unreadable: list[str] = field(default_factory=list)
    next_due_seconds: float | None = None
    notes: list[str] = field(default_factory=list)


def plan(runs: dict, now: datetime) -> Plan:
    """Decide what to dispatch.

    `runs` maps a workflow file to its newest run ({"status", "created_at"}),
    to NEVER when it has no runs, or to None when the list could not be read.
    """
    result = Plan()
    busy_groups = set()
    for group, members in GROUP_MEMBERS.items():
        for wf in members:
            newest = runs.get(wf)
            if newest is None:
                busy_groups.add(group)  # unknown is treated as busy, never as idle
                result.notes.append(f"{wf}: run list unreadable - group {group} treated as busy")
            elif newest != NEVER and newest["status"] != "completed":
                busy_groups.add(group)
                result.in_progress = True

    due = []
    for job in SCHEDULE:
        wf = job["file"]
        newest = runs.get(wf)
        if newest is None:
            result.unreadable.append(wf)
            continue
        if newest == NEVER:
            due.append((float("inf"), job))
            continue
        if newest["status"] != "completed":
            continue
        age = (now - newest["created_at"]).total_seconds()
        interval = job["minutes"] * 60
        if age >= interval:
            due.append((age / interval, job))
        else:
            wait = interval - age
            if result.next_due_seconds is None or wait < result.next_due_seconds:
                result.next_due_seconds = wait

    for group in dict.fromkeys(job["group"] for _, job in due):
        in_group = sorted((d for d in due if d[1]["group"] == group), key=lambda d: -d[0])
        pick = in_group[0][1]["file"]
        for _, other in in_group[1:]:
            result.notes.append(f"{other['file']}: due, but {pick} is more overdue in {group}")
        if group in busy_groups:
            result.notes.append(f"{pick}: due, but group {group} is busy - not queueing")
            continue
        result.dispatch.append(pick)
    return result


def gate(wf: str, runs: dict, now: datetime) -> tuple[bool, str]:
    """Should a CRON-started run of `wf` go ahead? `runs` excludes that run.

    A concurrency group holds one pending run, so a cron run arriving while the
    group is busy evicts whatever was already waiting: measured 2026-09-30,
    trace's 03:04 cron evicted that day's only analyze run, and the keeper's
    hourly cron and its self-dispatch evicted each other four times in a day.
    The keeper never dispatches into a busy group; the crons had no such rule.
    So a cron run steps aside when its group is busy or the keeper has already
    run it within its interval, and still runs when the keeper is dead, which
    is what the crons are kept for. Unreadable counts as idle: failing OPEN
    keeps the fallback alive, and at worst reproduces the old behaviour.
    """
    group = next((g for g, members in GATE_GROUPS.items() if wf in members), None)
    for member in GATE_GROUPS.get(group, [wf]):
        newest = runs.get(member)
        if newest not in (None, NEVER) and newest["status"] != "completed":
            return False, f"{member} is {newest['status']} in group {group}"
    job = next((j for j in SCHEDULE if j["file"] == wf), None)
    own = runs.get(wf)
    if job and own not in (None, NEVER):
        age = (now - own["created_at"]).total_seconds()
        if age < job["minutes"] * 60:
            return False, f"last run {age / 60:.0f} min ago, interval {job['minutes']} min"
    return True, "group idle and due"


# --- GitHub I/O -----------------------------------------------------------------


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


# A run that did no work must not count as the workflow having run: the
# interval would restart from it and the real run slip a whole interval.
# Measured 2026-09-30: the day's only analyze run was evicted from the group
# (cancelled) at 03:04, and as the newest run it told the keeper analyze had
# run - so nothing would have re-dispatched it for 24 hours.
NO_WORK_CONCLUSIONS = {"cancelled", "skipped"}
LOOKBACK = 10
_no_work_cache: dict[int, bool] = {}


def did_no_work(s: requests.Session, repo: str, row: dict) -> bool:
    """A completed run that was evicted, or a cron run its gate stepped aside.

    A gated-off cron run concludes `success` with its work job skipped, so the
    run's own conclusion cannot tell; its jobs can. Only completed `schedule`
    runs are asked, once each. Unreadable counts as work done - the cautious
    side, since it can only delay a dispatch by one interval, never double one.
    """
    if row.get("status") != "completed":
        return False
    if row.get("conclusion") in NO_WORK_CONCLUSIONS:
        return True
    if row.get("event") != "schedule":
        return False
    run_id = row.get("id")
    if run_id not in _no_work_cache:
        try:
            r = s.get(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
                      timeout=(10, 30))
            r.raise_for_status()
            work = [j for j in r.json().get("jobs", []) if j.get("name") != "gate"]
            _no_work_cache[run_id] = bool(work) and all(
                j.get("conclusion") == "skipped" for j in work)
        except (requests.RequestException, ValueError) as exc:
            print(f"[keeper] run {run_id}: could not read jobs ({exc})")
            return False
    return _no_work_cache[run_id]


def newest_run(s: requests.Session, repo: str, wf: str, exclude_run_id: int | None = None):
    """The newest run of `wf` that did work (or is still going), NEVER if it
    has none in the last LOOKBACK, None if unreadable."""
    try:
        r = s.get(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs",
                  params={"per_page": LOOKBACK}, timeout=(10, 30))
        r.raise_for_status()
        rows = r.json().get("workflow_runs")
    except (requests.RequestException, ValueError) as exc:
        print(f"[keeper] {wf}: could not read runs ({exc})")
        return None
    if rows is None:
        return None
    for row in rows:
        if row.get("id") == exclude_run_id or did_no_work(s, repo, row):
            continue
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        return {"status": row["status"], "created_at": created}
    return NEVER


def dispatch(s: requests.Session, repo: str, wf: str, ref: str) -> bool:
    try:
        r = s.post(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches",
                   json={"ref": ref}, timeout=(10, 30))
    except requests.RequestException as exc:
        print(f"[keeper] {wf}: dispatch FAILED ({exc})")
        return False
    if r.status_code != 204:
        print(f"[keeper] {wf}: dispatch FAILED (HTTP {r.status_code}: {r.text[:200]})")
        return False
    return True


def tick(s, repo: str, ref: str, dry_run: bool) -> Plan:
    files = {wf for members in GROUP_MEMBERS.values() for wf in members}
    runs = {wf: newest_run(s, repo, wf) for wf in sorted(files)}
    result = plan(runs, datetime.now(UTC))
    for note in result.notes:
        print(f"[keeper] {note}")
    for wf in result.dispatch:
        if dry_run:
            print(f"[keeper] {wf}: WOULD dispatch [dry run]")
        elif dispatch(s, repo, wf, ref):
            print(f"[keeper] {wf}: dispatched")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Jayesh137/Ezekiel"))
    ap.add_argument("--ref", default="main")
    ap.add_argument("--budget-seconds", type=int, default=60 * 60,
                    help="tick for this long, then exit so keeper.yml re-dispatches")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gate", metavar="WORKFLOW",
                    help="decide whether a cron-started run of WORKFLOW should proceed; "
                         "writes run=true|false to $GITHUB_OUTPUT and always exits 0")
    args = ap.parse_args()

    if args.gate:
        return run_gate(args.gate, args.repo)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[keeper] no GH_TOKEN/GITHUB_TOKEN")
        return 1
    s = _session(token)
    deadline = time.monotonic() + args.budget_seconds
    while True:
        wait = next_sleep(tick(s, args.repo, args.ref, args.dry_run))
        left = deadline - time.monotonic()
        if left <= 0:
            print("[keeper] budget spent - exiting for the next keeper run")
            return 0
        wait = min(wait, left)
        print(f"[keeper] next look in {wait:.0f}s")
        time.sleep(wait)


def run_gate(wf: str, repo: str) -> int:
    proceed, reason = True, "gate could not decide - failing open"
    try:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        own_id = int(os.environ.get("GITHUB_RUN_ID") or 0) or None
        s = _session(token or "")
        group = next((g for g, members in GATE_GROUPS.items() if wf in members), None)
        members = GATE_GROUPS.get(group, [wf])
        runs = {m: newest_run(s, repo, m, exclude_run_id=own_id) for m in members}
        proceed, reason = gate(wf, runs, datetime.now(UTC))
    except Exception as exc:  # noqa: BLE001 - the gate must never be what fails a run
        print(f"[gate] {type(exc).__name__}: {exc}")
    print(f"[gate] {wf}: {'RUN' if proceed else 'SKIP'} - {reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write("run=true\n" if proceed else "run=false\n")
    return 0


def next_sleep(result: Plan) -> float:
    """Seconds until the next tick is worth making."""
    if result.dispatch or result.in_progress or result.next_due_seconds is None:
        # Something is running (or nothing readable is scheduled): poll, since
        # nothing will tell us when it finishes.
        return POLL_SECONDS
    return max(5.0, min(result.next_due_seconds + 5, MAX_SLEEP_SECONDS))


if __name__ == "__main__":
    sys.exit(main())
