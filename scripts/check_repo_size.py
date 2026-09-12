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


def scan(root: Path | str = ".") -> dict:
    """Every file at or above the warning band, worst first. Pure apart from I/O."""
    root = Path(root)
    over_limit, warning = [], []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(root).parts):
            continue
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


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    report = scan(argv[0] if argv else ".")

    for f in report["warning"]:
        print(f"[size] WARNING {f['path']} is {f['mib']} MiB ({f['mb']} MB) — "
              f"GitHub refuses a file at {HARD_LIMIT_MIB} MiB")
    for f in report["over_limit"]:
        print(f"[size] OVER LIMIT {f['path']} is {f['mib']} MiB ({f['mb']} MB), "
              f"above GitHub's {HARD_LIMIT_MIB} MiB limit — this push WILL be "
              f"rejected and the run's reading lost")

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
