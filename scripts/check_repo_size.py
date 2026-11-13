#!/usr/bin/env python3
"""Catch a file outgrowing GitHub BEFORE the push, not in a pre-receive hook.

On 2026-09-12 `data/transfer_graph/latest.json` crossed 100 MiB and every "Trace
Fund Flows" run began failing: the graph was computed, the push was declined, and
the run's whole reading was thrown away. Discovery is the only vector that
reaches an address nobody has seen, and it had stopped being able to save
anything.

The diagnosis was buried. The push step retries, so the log holds five identical
GH001 rejections and the one sentence naming the file scrolls past in the middle
of them. And nothing was watching the size at all: `test.yml` runs only on
pushes touching src/, tests/ or scripts/, so it never sees a data commit, while
this repo commits its own output on every run.

Two thresholds, because a check that only fires at the limit is the failure with
extra steps:

  HARD  100 MiB  GitHub refuses the push. Fails the step, loudly and early.
  WARN   80 MiB  Still pushes. Says so now, while there is room to act.

Note the unit. The limit is 100 MEBIbytes = 104.86 MB, which is why the failure
looked sudden: the version on origin was 104.23 MB — 99.4 MiB — and under it.
A check written in decimal MB would have cried wolf for days and been ignored.
"""

import os
import sys
from pathlib import Path

# GitHub rejects any blob above this. Not a tunable — it is their number.
HARD_LIMIT_MIB = 100
# Enough room that a file growing a few MB a day is reported well before it
# blocks a push. The substrate's biggest finished day is 73.1 MiB.
WARN_LIMIT_MIB = 80

MIB = 1024 * 1024

# Never scanned: not pushed as blobs, and routinely larger than the limit.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             ".pytest_cache", ".ruff_cache", "build", ".svelte-kit"}
LOCAL_ONLY = {('data', '.local'), ('.superpowers', 'sdd')}


def scan(root: Path | str = ".") -> dict:
    """Every file at or above the warning band, worst first. Pure apart from I/O."""
    root = Path(root)
    over_limit, warning = [], []
    # Prune ignored trees before walking them: raw SQLite artifacts and local
    # replay fixtures are never Git blobs and must not block JSON publication.
    paths = []
    for directory, subdirs, files in os.walk(root):
        relative = Path(directory).relative_to(root).parts
        subdirs[:] = [name for name in subdirs if name not in SKIP_DIRS
                      and (*relative, name) not in LOCAL_ONLY]
        paths.extend(Path(directory) / name for name in files)
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        mib = size / MIB
        if mib < WARN_LIMIT_MIB:
            continue
        finding = {"path": path.relative_to(root).as_posix(),
                   "bytes": size, "mib": round(mib, 2), "mb": round(size / 1e6, 2)}
        (over_limit if mib >= HARD_LIMIT_MIB else warning).append(finding)
    over_limit.sort(key=lambda f: -f["bytes"])
    warning.sort(key=lambda f: -f["bytes"])
    return {"over_limit": over_limit, "warning": warning, "ok": not over_limit}


def _git_restore(root: Path, rel: str) -> bool:
    """Put the committed version of `rel` back. False when it has none."""
    import subprocess
    done = subprocess.run(["git", "checkout", "HEAD", "--", rel], cwd=root,
                          capture_output=True, text=True)
    return done.returncode == 0


def _git_exclude(root: Path, rel: str) -> None:
    """Keep a never-committed file out of `git add` without deleting it."""
    info = root / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    with open(info / "exclude", "a", encoding="utf-8") as fh:
        fh.write(os.linesep + "/" + rel + os.linesep)


def _page(findings: list[dict]) -> None:
    """HIGH, once a day per file: a capability of ours, not a fact about him."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from src import alerts
        for f in findings:
            alerts._send_with_cooldown(
                f"repo_oversize:{f['path']}", 24,
                f"HIGH: Output file too large to store ({f['path']})",
                f"{f['path']} is {f['mib']} MiB, above GitHub's "
                f"{HARD_LIMIT_MIB} MiB limit. This run kept the previous "
                f"committed version of that ONE file and committed everything "
                f"else. The file stays stale until its writer is given a byte "
                f"budget.")
    except Exception as exc:  # the commit matters more than the page
        print(f"[size] could not page about the held-back file(s): {exc}")


def hold_back(root: Path | str, findings: list[dict], restore=_git_restore,
              exclude=_git_exclude) -> list[dict]:
    """Take each over-limit file out of THIS commit, and nothing else.

    Refusing the whole push because one file is too big threw away every
    other reading of the run: from 2026-10-28 to 2026-11-13 the transfer graph
    sat at 100.39 MiB and each trace run computed the roster, the casebook,
    dormancy, provenance, the execution program and accounting, then committed
    none of them. One file's size is that file's failure. A tracked file is put
    back to its committed version (stale, and its feed-staleness alarm says
    so); a file never committed is excluded from `git add`.
    """
    root = Path(root)
    held = []
    for f in findings:
        how = "restored" if restore(root, f["path"]) else "excluded"
        if how == "excluded":
            exclude(root, f["path"])
        held.append({**f, "held": how})
    return held


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    holding = "--hold-back" in argv
    argv = [a for a in argv if a != "--hold-back"]
    report = scan(argv[0] if argv else ".")

    for f in report["warning"]:
        print(f"[size] WARNING {f['path']} is {f['mib']} MiB ({f['mb']} MB) — "
              f"GitHub refuses a file at {HARD_LIMIT_MIB} MiB")
    for f in report["over_limit"]:
        print(f"[size] OVER LIMIT {f['path']} is {f['mib']} MiB ({f['mb']} MB), "
              f"above GitHub's {HARD_LIMIT_MIB} MiB limit — this push WILL be "
              f"rejected and the run's reading lost")

    if not report["ok"] and holding:
        held = hold_back(argv[0] if argv else ".", report["over_limit"])
        for f in held:
            print(f"::error::[size] {f['path']} ({f['mib']} MiB) held back from "
                  f"this commit ({f['held']}); every other file is committed")
        _page(held)
        return 0
    if not report["ok"]:
        print("[size] refusing to push: shrink the file(s) above first. "
              "Nothing is committed, so this run's data is still on the runner.")
        return 1
    if not report["warning"]:
        print(f"[size] no file within {HARD_LIMIT_MIB - WARN_LIMIT_MIB} MiB "
              f"of the limit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
