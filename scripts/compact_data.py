# scripts/compact_data.py
"""Compact derivable data so the repo stops strangling the collection cadence.

Every workflow run begins with a checkout of the whole data tree. At 749 MB that
checkout dominated a 4-minute job budget, which is why the */5 cron produced
~12-17 runs/day instead of 288.

Two targets, both DERIVABLE — nothing irreplaceable is touched:

  scans/      278 MB. Each hourly scan embeds a full fingerprint summary for its
              top 20 results. Only latest.json needs those; the dated history
              needs scores and evidence. Stripping them is lossless for every
              consumer (the dashboard reads latest.json; backtest reads
              latest.json).

  snapshots/  positions, account, spot, positions_hip3_xyz, orders — per-minute
              full-state dumps. Days older than KEEP_LIVE_DAYS are rolled into
              one gzipped JSONL per day. For account/ a tiny plain-JSON daily
              summary is also written so chart history survives in a directly
              fetchable form.

fills, funding, ledger and l1_transactions are NEVER touched: Hyperliquid only
serves ~2000 recent entries per endpoint, so those cannot be re-fetched.

Every deletion is preceded by a read-back verification of the archive.

Usage:
    python scripts/compact_data.py --dry-run     # measure, change nothing
    python scripts/compact_data.py --apply
"""

import argparse
import gzip
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import DATA_DIR

# Never compact these — they cannot be re-fetched from the API.
IRREPLACEABLE = {"fills", "funding", "ledger", "l1_transactions", "state",
                 "candidates", "calibration"}

# Snapshot directories laid out as {type}/YYYY-MM-DD/HH-MM.json
SNAPSHOT_TYPES = ["positions", "account", "spot", "positions_hip3_xyz", "orders"]

# Keep this many recent days directly fetchable by the dashboard.
KEEP_LIVE_DAYS = 7

# Historical sweeps keep only their head; the tail adds size without readers.
MAX_HISTORIC_RESULTS = 25


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# --- scans ---------------------------------------------------------------------

def compact_scans(dry_run: bool = True) -> dict:
    """Strip embedded candidate fingerprints from dated scan files.

    latest.json keeps its fingerprints (the backtest scores strangers from it).
    """
    scans_dir = DATA_DIR / "scans"
    if not scans_dir.exists():
        return {"before": 0, "after": 0, "files": 0}

    before = _dir_size(scans_dir)
    touched = 0

    for path in sorted(scans_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  ! skipping unreadable {path.name}: {e}")
            continue

        entries = data if isinstance(data, list) else [data]
        stripped = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for r in entry.get("results", []):
                if isinstance(r, dict) and r.pop("fingerprint", None) is not None:
                    stripped += 1
        if not stripped:
            continue

        touched += 1
        if not dry_run:
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            tmp.replace(path)

    after = _dir_size(scans_dir) if not dry_run else before
    return {"before": before, "after": after, "files": touched}


# --- snapshots -----------------------------------------------------------------

def _account_summary_row(hhmm: str, payload: dict) -> dict | None:
    """Chart-relevant fields only — keeps history usable at ~1% of the size."""
    perp = payload.get("perp", payload) or {}
    ms = perp.get("marginSummary") or perp.get("crossMarginSummary") or {}
    if not ms:
        return None
    pnl = 0.0
    for ap in perp.get("assetPositions", []) or []:
        try:
            pnl += float((ap or {}).get("position", {}).get("unrealizedPnl", 0) or 0)
        except (TypeError, ValueError):
            pass
    try:
        return {
            "t": hhmm,
            "av": round(float(ms.get("accountValue", 0) or 0), 2),
            "pnl": round(pnl, 2),
            "mu": round(float(ms.get("totalMarginUsed", 0) or 0), 2),
            "ntl": round(float(ms.get("totalNtlPos", 0) or 0), 2),
        }
    except (TypeError, ValueError):
        return None


def archive_snapshot_type(data_type: str, dry_run: bool = True,
                          keep_days: int = KEEP_LIVE_DAYS) -> dict:
    """Roll dated snapshot subdirectories into one gzipped JSONL per day."""
    type_dir = DATA_DIR / data_type
    if not type_dir.exists():
        return {"before": 0, "after": 0, "days": 0, "snapshots": 0}

    before = _dir_size(type_dir)
    cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    archive_dir = type_dir / "archive"
    summary_dir = type_dir / "daily"

    day_dirs = sorted(d for d in type_dir.iterdir()
                      if d.is_dir() and len(d.name) == 10 and d.name < cutoff)

    days = 0
    total_snaps = 0
    for day in day_dirs:
        files = sorted(day.glob("*.json"))
        if not files:
            continue
        records = []
        summary = []
        for fp in files:
            try:
                with open(fp) as f:
                    payload = json.load(f)
            except (OSError, ValueError) as e:
                print(f"  ! skipping unreadable {fp}: {e}")
                continue
            hhmm = fp.stem
            records.append({"t": hhmm, "d": payload})
            if data_type == "account":
                row = _account_summary_row(hhmm, payload)
                if row:
                    summary.append(row)

        if not records:
            continue
        days += 1
        total_snaps += len(records)

        if dry_run:
            continue

        archive_dir.mkdir(parents=True, exist_ok=True)
        out = archive_dir / f"{day.name}.jsonl.gz"
        with gzip.open(out, "wt", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec, separators=(",", ":")) + "\n")

        # Verify the archive reads back with the same record count BEFORE deleting.
        read_back = 0
        with gzip.open(out, "rt", encoding="utf-8") as gz:
            for _ in gz:
                read_back += 1
        if read_back != len(records):
            raise RuntimeError(
                f"archive verification FAILED for {day}: wrote {len(records)} "
                f"records, read back {read_back} — refusing to delete source")

        if summary:
            summary_dir.mkdir(parents=True, exist_ok=True)
            with open(summary_dir / f"{day.name}.json", "w") as f:
                json.dump(summary, f, separators=(",", ":"))

        shutil.rmtree(day)

    after = _dir_size(type_dir) if not dry_run else before
    return {"before": before, "after": after, "days": days, "snapshots": total_snaps}


# --- cross-file record dedup ----------------------------------------------------

def dedupe_across_days(data_type: str, key_field: str, dry_run: bool = True) -> dict:
    """Remove records duplicated across daily files, keeping the earliest copy.

    append_records() dedupes only within a single day's file. `historicalOrders`
    returns the last ~2000 orders on every poll, so the same order was rewritten
    into every subsequent daily file: 651,116 oid mentions across data/orders for
    29,791 distinct orders (~22x). Nothing is lost — the surviving copy is the
    first-observed one.
    """
    type_dir = DATA_DIR / data_type
    if not type_dir.exists():
        return {"before": 0, "after": 0, "removed": 0, "kept": 0}

    before = _dir_size(type_dir)
    seen: set[str] = set()
    removed = 0
    kept = 0

    for path in sorted(type_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            with open(path) as f:
                records = json.load(f)
        except (OSError, ValueError) as e:
            print(f"  ! skipping unreadable {path.name}: {e}")
            continue
        if not isinstance(records, list):
            continue

        out = []
        for r in records:
            if not isinstance(r, dict):
                out.append(r)
                continue
            key = str(r.get(key_field, ""))
            if key and key in seen:
                removed += 1
                continue
            if key:
                seen.add(key)
            out.append(r)
        kept += len(out)

        if dry_run or len(out) == len(records):
            continue
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(out, f, separators=(",", ":"))
        tmp.replace(path)

    after = _dir_size(type_dir) if not dry_run else before
    return {"before": before, "after": after, "removed": removed, "kept": kept}


def trim_scan_history(dry_run: bool = True) -> dict:
    """Reduce dated scan files to the fields anyone actually reads.

    Per-result `dimensions` and `evidence` are only consulted for the current
    sweep (latest.json, which is left untouched). Per-candidate score history is
    independently persisted in data/candidates/*.json, so trend analysis survives.
    """
    scans_dir = DATA_DIR / "scans"
    if not scans_dir.exists():
        return {"before": 0, "after": 0, "files": 0}

    before = _dir_size(scans_dir)
    touched = 0
    for path in sorted(scans_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue

        entries = data if isinstance(data, list) else [data]
        changed = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            slim = []
            for r in entry.get("results", []):
                if not isinstance(r, dict):
                    continue
                slim.append({
                    "wallet": r.get("wallet"),
                    "score": r.get("score"),
                    "tier": (r.get("evidence") or {}).get("tier"),
                    "source": r.get("source"),
                })
                changed = True
            if slim:
                # Only the head of each historical sweep is worth keeping: the
                # per-wallet score series lives in data/candidates/, and the null
                # distribution lives in data/calibration/population.json.
                slim.sort(key=lambda r: r.get("score") or 0, reverse=True)
                entry["results"] = slim[:MAX_HISTORIC_RESULTS]
        if not changed:
            continue
        touched += 1
        if not dry_run:
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            tmp.replace(path)

    after = _dir_size(scans_dir) if not dry_run else before
    return {"before": before, "after": after, "files": touched}


def slim_daily_records(data_type: str, keep_fields: list[str],
                       dry_run: bool = True) -> dict:
    """Reduce appended history records to a field subset.

    collect_fees/collect_rate_limit used to append their whole API payload every
    run — the full fee schedule plus a 30-day volume table, ~5 KB a time, with no
    reader beyond latest.json (which is left intact).
    """
    type_dir = DATA_DIR / data_type
    if not type_dir.exists():
        return {"before": 0, "after": 0, "files": 0}

    before = _dir_size(type_dir)
    touched = 0
    for path in sorted(type_dir.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            with open(path) as f:
                records = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(records, list):
            continue
        slim = [{k: r.get(k) for k in keep_fields if k in r}
                for r in records if isinstance(r, dict)]
        if slim == records:
            continue
        touched += 1
        if not dry_run:
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(slim, f, separators=(",", ":"))
            tmp.replace(path)

    after = _dir_size(type_dir) if not dry_run else before
    return {"before": before, "after": after, "files": touched}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="measure only")
    g.add_argument("--apply", action="store_true", help="perform compaction")
    ap.add_argument("--keep-days", type=int, default=KEEP_LIVE_DAYS)
    args = ap.parse_args()
    dry = args.dry_run

    print(f"{'DRY RUN — no changes' if dry else 'APPLYING COMPACTION'}")
    print(f"Protected (never touched): {', '.join(sorted(IRREPLACEABLE))}\n")

    total_before = _dir_size(DATA_DIR)

    print("scans: stripping embedded candidate fingerprints from dated files")
    s = compact_scans(dry)
    print(f"  {s['files']} file(s) with fingerprints, dir was {_fmt(s['before'])}"
          + ("" if dry else f" -> {_fmt(s['after'])}"))

    print("scans: trimming dated results to wallet/score/tier")
    t2 = trim_scan_history(dry)
    print(f"  {t2['files']} file(s), dir {_fmt(t2['before'])}"
          + ("" if dry else f" -> {_fmt(t2['after'])}"))

    for data_type, fields in (
            ("fees", ["_ts", "userCrossRate", "userAddRate", "activeReferralDiscount"]),
            ("rate_limit", ["_ts", "cumVlm", "nRequestsUsed"])):
        sl = slim_daily_records(data_type, fields, dry)
        if sl["files"]:
            print(f"{data_type}: slimmed {sl['files']} history file(s)  {_fmt(sl['before'])}"
                  + ("" if dry else f" -> {_fmt(sl['after'])}"))

    for data_type, key in (("orders", "oid"), ("fees", "_ts"), ("rate_limit", "_ts")):
        d = dedupe_across_days(data_type, key, dry)
        if d["removed"]:
            print(f"{data_type}: {d['removed']} cross-file duplicate(s) removed, "
                  f"{d['kept']} kept  {_fmt(d['before'])}"
                  + ("" if dry else f" -> {_fmt(d['after'])}"))

    for t in SNAPSHOT_TYPES:
        r = archive_snapshot_type(t, dry, args.keep_days)
        if r["days"]:
            print(f"{t}: {r['days']} day(s) / {r['snapshots']} snapshots -> gzip archive"
                  f"  {_fmt(r['before'])}" + ("" if dry else f" -> {_fmt(r['after'])}"))

    if not dry:
        total_after = _dir_size(DATA_DIR)
        print(f"\ndata/ {_fmt(total_before)} -> {_fmt(total_after)} "
              f"({_fmt(total_before - total_after)} reclaimed)")
    else:
        print(f"\ndata/ currently {_fmt(total_before)} — re-run with --apply")


if __name__ == "__main__":
    main()
