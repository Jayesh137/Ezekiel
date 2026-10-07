#!/usr/bin/env python3
"""Run one scheduled job on the always-on VM: detectors, then commit the data.

Invoked by a systemd timer as `run.py <job>`. It replaces, on a persistent host,
the GitHub Actions run + the three schedulers (keeper, PC dispatcher, Apps Script
relay) that existed only because Actions runners are ephemeral and their crons are
best-effort. See deploy/vm/README.md and the platform-migration spec.

Invariants carried over from the workflows, because each was bought with a real
outage (docs/incident-log.md):

- **One writer at a time.** A single global lock serialises whole job runs, so no
  two jobs ever mutate the shared working tree or push concurrently. This trades a
  little watch latency (it may wait behind a trace run) for zero concurrency
  hazard — a strict improvement on the Actions median of 198 minutes between runs.
- **A step timeout is survivable; a job death is not.** Each step is killed at its
  own budget and the run carries on, then the commit persists whatever completed.
  Every writer rewrites its own file whole, so a file not updated this run keeps
  its previous value.
- **The push never discards a reading.** `check_repo_size` runs before anything is
  committed (a >100 MiB blob is refused after the work is done), and a lost push
  race is settled with `git pull --rebase -X theirs --autostash` — "theirs" in a
  rebase is our replayed commit, so our files win and files only the other side
  changed are kept. During the migration Actions may still push; after cutover the
  VM is the sole writer and conflicts stop entirely.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.vm.jobs import JOBS  # noqa: E402

LOCK = Path(os.environ.get("EZEKIEL_LOCK", "/tmp/ezekiel-run.lock"))
PYTHON = os.environ.get("EZEKIEL_PYTHON", sys.executable)
COMMIT_MESSAGES = {
    "collect": "data: collect trading data [automated]",
    "watch": "data: watch wallets [automated]",
    "trace": "data: trace fund flows [automated]",
    "scan": "data: scanner results [automated]",
    "analyze": "data: daily analysis [automated]",
    "study": "data: study candidates [automated]",
}


def log(msg):
    print(f"{datetime.now(UTC).isoformat()} [{msg}]", flush=True)


def git(*args, check=True, capture=False):
    return subprocess.run(["git", *args], cwd=ROOT, check=check,
                          text=True, capture_output=capture)


def sync_repo():
    """Fast-forward to origin before working, settling any race with -X theirs."""
    git("fetch", "origin", "main", check=False)
    result = git("rebase", "-X", "theirs", "--autostash", "origin/main", check=False)
    if result.returncode != 0:
        git("rebase", "--abort", check=False)
        git("reset", "--hard", "origin/main", check=False)


def run_step(argv, timeout):
    cmd = [PYTHON, *argv[1:]] if argv and argv[0] == "python" else list(argv)
    started = time.monotonic()
    try:
        subprocess.run(cmd, cwd=ROOT, timeout=timeout, check=False)
        log(f"step ok {' '.join(argv)} in {time.monotonic() - started:.0f}s")
    except subprocess.TimeoutExpired:
        log(f"step TIMEOUT {' '.join(argv)} after {timeout}s — continuing")


def commit_data(message):
    size = subprocess.run([PYTHON, "scripts/check_repo_size.py", "."], cwd=ROOT, check=False)
    if size.returncode != 0:
        log("check_repo_size FAILED — not committing")
        return
    git("add", "data/", check=False)
    staged = git("diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        log("no data changes to commit")
        return
    git("-c", "user.name=ezekiel-vm", "-c", "user.email=ezekiel-vm@localhost",
        "commit", "-m", message, check=False)
    for attempt in range(1, 6):
        if git("push", "origin", "main", check=False).returncode == 0:
            log(f"pushed on attempt {attempt}")
            return
        git("fetch", "origin", "main", check=False)
        if git("rebase", "-X", "theirs", "--autostash", "FETCH_HEAD", check=False).returncode != 0:
            git("rebase", "--abort", check=False)
            log(f"attempt {attempt}: rebase could not settle automatically")
        time.sleep(3 + attempt)
    git("push", "origin", "main", check=False)


def run_job(name):
    job = JOBS[name]
    log(f"job {name} start")
    sync_repo()
    for argv, timeout in job["steps"]:
        run_step(argv, timeout)
    commit_data(COMMIT_MESSAGES.get(name, f"data: {name} [automated]"))
    log(f"job {name} done")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=list(JOBS))
    parser.add_argument("--no-lock", action="store_true", help="skip the global lock (tests)")
    args = parser.parse_args()
    if args.no_lock:
        run_job(args.job)
        return
    import fcntl  # Linux-only; the VM is Linux, and this keeps the module importable elsewhere.
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"job {args.job} skipped — another job holds the lock")
            return
        run_job(args.job)


if __name__ == "__main__":
    main()
