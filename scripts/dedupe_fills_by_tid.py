# scripts/dedupe_fills_by_tid.py
"""One-time migration: remove duplicate fills (same tid) from data/fills/*.json.

Dry-run by default — prints per-file counts only. Pass --apply to rewrite files.
Earliest occurrence of each tid wins (file order = date order, within-file order kept).
"""

import json
import sys
from pathlib import Path

FILLS_DIR = Path(__file__).parent.parent / "data" / "fills"


def main():
    apply = "--apply" in sys.argv
    seen: set = set()
    total_before = 0
    total_removed = 0
    no_tid = 0

    for fp in sorted(FILLS_DIR.glob("*.json")):
        if fp.name == "latest.json":
            continue
        with open(fp) as f:
            records = json.load(f)
        kept = []
        removed = 0
        for r in records:
            tid = r.get("tid")
            if tid is None:
                no_tid += 1
                kept.append(r)
                continue
            if tid in seen:
                removed += 1
            else:
                seen.add(tid)
                kept.append(r)
        total_before += len(records)
        total_removed += removed
        if removed:
            print(f"{fp.name}: {len(records)} -> {len(kept)} (-{removed})")
            if apply:
                with open(fp, "w") as f:
                    json.dump(kept, f, indent=2)

    print(f"\nTotal fills: {total_before}, duplicate tids removed: {total_removed}, "
          f"records without tid: {no_tid}")
    print("APPLIED." if apply else "DRY RUN — no files changed. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
