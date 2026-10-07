#!/usr/bin/env python3
"""Is the candidate study ready for Phase 2? Read-only.

Runs the gate in `src/study/readiness.py` on the data directory and prints its report
as JSON on stdout (a one-line summary on stderr). Exit 0: ready. Exit 1: not yet — the
report names the checks holding it back. Exit 2: an input the gate depends on could
not be read, which is never reported as "not ready": a failed read is not evidence
(rule 5). It writes nothing.

The Phase 2 routine runs this every morning and builds nothing unless it exits 0;
anyone can run it to see how far off Phase 2 is:

    python scripts/check_phase2_ready.py [--data-dir DIR] [--now-ms MS]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_study
from src import utils
from src.study import archive, panels, readiness, records, verdict

COVERAGE = ("fills", "orders", "ledger")


class Unreadable(Exception):
    """An input the gate depends on is missing or cannot be read."""


def read_strict(path: Path, *, required: bool):
    """The document at `path`; None when absent and not required; Unreadable when it is
    required and absent, or present but not readable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if required:
            raise Unreadable(f"{path.name} is missing") from None
        return None
    except (OSError, ValueError) as exc:
        raise Unreadable(f"{path.name} cannot be read: {exc}") from exc


def archive_coverage(rows: list[dict], data_dir: Path, now_ms: int) -> tuple[list, list]:
    """Wallets read at least once whose archive holds no record with coverage, and
    wallets whose archive will not read (acceptance 2)."""
    today = records.day_of(now_ms)
    first = records.day_of(now_ms - (run_study.WINDOW_DAYS - 1) * records.DAY_MS)
    without, unreadable = [], []
    for row in rows:
        if not row.get("last_read_ms"):
            continue
        try:
            days = archive.load_days(row["wallet"], first, today, data_dir)
        except archive.Unreadable:
            unreadable.append(row["wallet"])
            continue
        if not any((day.get("coverage") or {}).get(k) for day in days.values() for k in COVERAGE):
            without.append(row["wallet"])
    return without, unreadable


def false_for(rows: list[dict], data_dir: Path) -> list[dict]:
    """Wallets reading tooling FOR or MIXED while showing a trait he never shows (their
    T1 `against_traits`). A dossier that cannot be read does not clear its wallet."""
    out = []
    for row in rows:
        said = ((row.get("families") or {}).get("tooling") or {}).get("verdict")
        if said not in (verdict.FOR, verdict.MIXED):
            continue
        path = archive.root(data_dir) / "wallets" / f"{row['wallet']}.json"
        try:
            doc = read_strict(path, required=True)
            detail = (((doc or {}).get("tests") or {}).get("T1") or {}).get("detail") or {}
            traits = sorted(detail.get("against_traits") or [])
        except (Unreadable, AttributeError):
            traits = ["dossier unreadable"]
        if traits:
            out.append({"wallet": row["wallet"], "verdict": said, "traits": traits})
    return out


def evaluate(data_dir: Path, now_ms: int, config: dict) -> dict:
    target = config["target_wallet"].lower()
    exclude = {target, *filter(None, map(run_study._address,
                                         config.get("known_self_wallets") or []))}
    latest = read_strict(archive.root(data_dir) / "latest.json", required=True)
    census_state = read_strict(data_dir / "execution_program" / "census_state.json",
                               required=False) or {}
    if not isinstance(latest, dict) or not isinstance(census_state, dict):
        raise Unreadable("the study summary or the census state is not a JSON object")
    his = run_study.his_days(data_dir, target)
    if not his:
        raise Unreadable("no history of his to judge his months against")
    habits = census_state.get("habits")
    strangers = panels.strangers(habits, exclude=exclude)
    fams = panels.families(run_study.read_json(data_dir / "hl_surface" / "latest.json", {}),
                           habits, exclude=exclude)
    members = run_study.read_json(archive.root(data_dir) / "panel" / "families.json",
                                  {}).get("members") or {}
    pairs = panels.member_pairs(fams, {w: v for w, v in members.items() if isinstance(v, dict)})
    recall = readiness.self_recall(his, strangers, pairs, readiness.recent_months(now_ms))
    rows = [r for r in latest.get("wallets") or [] if isinstance(r, dict) and r.get("wallet")]
    without, unreadable = archive_coverage(rows, data_dir, now_ms)
    census = run_study.read_json(data_dir / "execution_program" / "census.json", {})
    return readiness.readiness(now_ms=now_ms, latest=latest, read_without_coverage=without,
                               unreadable_archives=unreadable, recall=recall,
                               false_for=false_for(rows, data_dir), census=census)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="read this data directory instead of data/")
    parser.add_argument("--now-ms", type=int, default=None, help="judge as of this time")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir or utils.DATA_DIR)
    now_ms = int(args.now_ms or time.time() * 1000)
    try:
        report = evaluate(data_dir, now_ms, utils.load_config())
    except Unreadable as exc:
        print(json.dumps({"ready": False, "unreadable": str(exc)}, indent=2))
        print(f"[phase2] cannot tell: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str))
    held = [name for name, c in report["checks"].items() if not c["ok"]]
    print("[phase2] " + ("READY" if report["ready"] else "not yet: " + ", ".join(held)),
          file=sys.stderr)
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
