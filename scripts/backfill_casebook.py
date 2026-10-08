#!/usr/bin/env python3
"""Recover every suspect the roster ever named, from git history (spec 2026-10-08 §10).

Walks every committed version of data/roster/latest.json, oldest first, through the
same ingest code the live update uses (origin "backfill"), so the casebook starts with
everything the project has ever suspected, including the leads today's roster has
forgotten. Run by hand:

    python scripts/backfill_casebook.py                       # refuses a casebook that has cases
    python scripts/backfill_casebook.py --rebuild             # start the casebook again from history
    python scripts/backfill_casebook.py --out DIR --limit 40  # a quick look under DIR/casebook

One version is held in memory at a time (the roster is ~7 MB); git serves them all
through one `git cat-file --batch` process.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils
from src.casebook import cases as casefile
from src.casebook import extract, ingest, report, score, store
from src.not_wallets import token_registry_addresses

ROSTER_PATH = "data/roster/latest.json"


class NotEmpty(RuntimeError):
    """The casebook already holds cases; --rebuild replaces them."""


def parse_log(text: str) -> list[tuple[str, int]]:
    """`git log --format="%H %ct"` lines -> [(sha, commit time in ms)]."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 40:
            try:
                out.append((parts[0], int(parts[1]) * 1000))
            except ValueError:
                continue
    return out


def roster_history(repo: Path, *, since: str | None = None, limit: int | None = None):
    """Yield (commit_ms, sha, doc) for each committed roster, oldest first; doc is None
    when that version cannot be read or parsed."""
    args = ["git", "-C", str(repo), "log", "--reverse", "--format=%H %ct"]
    if since:
        args += ["--since", since]
    log = subprocess.run(args + ["--", ROSTER_PATH], capture_output=True, text=True, check=True).stdout
    commits = parse_log(log)
    if limit:
        commits = commits[-limit:]
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for sha, commit_ms in commits:
            proc.stdin.write(f"{sha}:{ROSTER_PATH}\n".encode())
            proc.stdin.flush()
            header = proc.stdout.readline().decode().split()
            if len(header) != 3 or header[1] != "blob":
                yield commit_ms, sha, None
                continue
            data = proc.stdout.read(int(header[2]))
            proc.stdout.read(1)                       # the newline after each object
            try:
                doc = json.loads(data)
            except ValueError:
                doc = None
            yield commit_ms, sha, doc
    finally:
        proc.stdin.close()
        proc.wait()


def run_backfill(history, config: dict, *, out_dir, rebuild: bool = False, log=None) -> dict:
    """Build the casebook from `history`, an iterable of (commit_ms, label, roster_doc)."""
    base = store.root(out_dir)
    if (base / "cases").exists() and any((base / "cases").glob("0x*.json")):
        if not rebuild:
            raise NotEmpty(f"{base / 'cases'} already holds cases; pass --rebuild to start again from history")
        store.clear(out_dir)
    cases, events, rejected = {}, [], {}
    tokens = token_registry_addresses()
    stats = {"versions": 0, "skipped": 0}
    last_ms = None
    for commit_ms, label, doc in history:
        if not isinstance(doc, dict) or not isinstance(doc.get("wallets"), list):
            stats["skipped"] += 1
            continue
        at_ms = casefile.parse_ms(doc.get("computed_at")) or commit_ms
        if last_ms is not None and at_ms <= last_ms:
            stats["skipped"] += 1
            continue
        merged = ingest.apply_roster(cases, doc, config, at_ms=at_ms, origin="backfill",
                                     tokens=tokens, rejected=rejected)
        events += merged["events"]
        stats["versions"] += 1
        last_ms = at_ms
        if log and stats["versions"] % 50 == 0:
            log(f"[backfill] {stats['versions']} versions read, {len(cases)} cases, through "
                f"{casefile.iso(at_ms)} ({label})")
    if last_ms is None:
        return {**stats, "cases": 0, "events": 0}
    scores = score.score_all(cases, extract.cluster_of(config))
    for address, case in cases.items():
        events += casefile.record_score(case, scores[address], at_ms=last_ms, origin="backfill")
    casefile.touch(cases, events)
    calibration = score.calibration(cases, scores)
    run_info = {"roster_status": "backfill", **stats, "events": len(events)}
    index = report.build_index(cases, scores, rejected=rejected, run=run_info, target={},
                               calibration=calibration, now_iso=casefile.iso(last_ms))
    store.write_cases(cases, out_dir)
    store.append_events(events, out_dir)
    store.write_index(index, out_dir)
    store.write_state({"roster_computed_at_ms": last_ms,
                       "rejected": sorted(rejected.values(), key=lambda r: r.get("last_seen") or "",
                                          reverse=True)[:ingest.MAX_REJECTED],
                       "backfill": {**stats, "cases": len(cases), "events": len(events)}}, out_dir)
    return {**stats, "cases": len(cases), "events": len(events), "calibration": calibration}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the casebook from the roster's git history.")
    parser.add_argument("--rebuild", action="store_true", help="replace an existing casebook")
    parser.add_argument("--out", type=Path, help="write under OUT/casebook instead of data/casebook")
    parser.add_argument("--since", help="only versions committed since this date (git --since)")
    parser.add_argument("--limit", type=int, help="only the newest N versions")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    history = roster_history(repo, since=args.since, limit=args.limit)
    try:
        out = run_backfill(history, utils.load_config(), out_dir=args.out or utils.DATA_DIR,
                           rebuild=args.rebuild, log=lambda line: print(line, flush=True))
    except NotEmpty as exc:
        print(f"[backfill] {exc}")
        return 2
    print(f"[backfill] {out['versions']} roster versions ({out['skipped']} skipped) -> "
          f"{out['cases']} cases, {out['events']} events")
    for row in (out.get("calibration") or {}).get("recall") or []:
        print(f"[backfill] recall: known {row['address'][:12]}... ranks {row['rank_among_unknown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
