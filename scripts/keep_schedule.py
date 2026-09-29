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
]

# Every workflow in each concurrency group, including ones never dispatched here.
GROUP_MEMBERS = {
    "watch": ["watch.yml"],
    "tape": ["tape.yml"],
    "data-commit": [
        "collect.yml", "trace.yml", "scan.yml", "analyze.yml",
        "backfill.yml", "substrate-backfill.yml",
    ],
}

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


# --- GitHub I/O -----------------------------------------------------------------


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


def newest_run(s: requests.Session, repo: str, wf: str):
    """The newest run of `wf`, NEVER if it has none, None if unreadable."""
    try:
        r = s.get(f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs",
                  params={"per_page": 1}, timeout=(10, 30))
        r.raise_for_status()
        rows = r.json().get("workflow_runs")
    except (requests.RequestException, ValueError) as exc:
        print(f"[keeper] {wf}: could not read runs ({exc})")
        return None
    if rows is None:
        return None
    if not rows:
        return NEVER
    created = datetime.fromisoformat(rows[0]["created_at"].replace("Z", "+00:00"))
    return {"status": rows[0]["status"], "created_at": created}


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
    args = ap.parse_args()

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


def next_sleep(result: Plan) -> float:
    """Seconds until the next tick is worth making."""
    if result.dispatch or result.in_progress or result.next_due_seconds is None:
        # Something is running (or nothing readable is scheduled): poll, since
        # nothing will tell us when it finishes.
        return POLL_SECONDS
    return max(5.0, min(result.next_due_seconds + 5, MAX_SLEEP_SECONDS))


if __name__ == "__main__":
    sys.exit(main())
