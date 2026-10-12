#!/usr/bin/env python3
"""Send the daily digest once a day (src/digest.py). Run by watch.yml every run.

Only the first run at or after DIGEST_HOUR_UTC sends; the day is remembered in a
committed cursor (`data/state/digest_sent_day.txt`) so the ~10-minute watch
cadence sends exactly one. Delivery goes through `alerts.send_notice` at LOW
priority — it never buzzes and never touches alert-delivery health, because a
digest that fails to arrive says nothing about the channel a CRITICAL needs.
The rendered digest is also written to `data/digest/latest.json` for the
dashboard. `--force` sends regardless of the cursor; `--dry-run` prints only.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import digest
from src.utils import (
    DATA_DIR,
    account_value_components,
    load_config,
    read_cursor_text,
    save_latest,
    write_cursor_text,
)

CURSOR = "digest_sent_day"
OUT_DIR = DATA_DIR / "digest"


def _read(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def newest_fill_ms():
    """The newest stored fill of the target, from the index's day list (no load)."""
    try:
        from src.fingerprint import load_fills
        times = [int(f.get("time")) for f in load_fills() if isinstance(f, dict)
                 and isinstance(f.get("time"), (int, float))]
        return max(times) if times else None
    except Exception:  # noqa: BLE001 - a digest must never fail the watch
        return None


def load_readings(data_dir: Path = DATA_DIR) -> dict:
    return {
        "dormancy": _read(data_dir / "dormancy" / "latest.json"),
        "account_value": account_value_components(_read(data_dir / "account" / "latest.json")),
        "fills_newest_ms": newest_fill_ms(),
        "roster": _read(data_dir / "roster" / "latest.json"),
        "casebook": _read(data_dir / "casebook" / "latest.json"),
        "watchlist": _read(data_dir / "watchlist" / "latest.json"),
        "census": _read(data_dir / "execution_program" / "census.json"),
        "tape_census": _read(data_dir / "tape" / "census.json"),
        "tape": _read(data_dir / "tape" / "latest.json"),
        "execution": _read(data_dir / "execution_program" / "latest.json"),
        "alerts": _read(data_dir / "alerts" / "latest.json"),
        "feed_other": _read(data_dir / "feed_health" / "other.json"),
        "feed_watch": _read(data_dir / "feed_health" / "watch.json"),
        "index": _read(data_dir / "index.json"),
        "risk": _read(data_dir / "risk" / "latest.json"),
        "scans": _read(data_dir / "scans" / "latest.json"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="send even if today's was sent")
    parser.add_argument("--dry-run", action="store_true", help="print the digest, send nothing")
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    last = read_cursor_text(CURSOR)
    if not args.force and not args.dry_run and not digest.due(last, now):
        print(f"[digest] not due (last sent {last or 'never'}, hour {now.hour:02d} UTC)")
        return 0
    built = digest.build(load_readings(), load_config(), now)
    title, body = digest.render(built)
    save_latest(str(OUT_DIR), {**built, "title": title, "body": body})
    print(f"[digest] {title}\n{body}")
    if args.dry_run:
        return 0
    from src.alerts import send_notice
    via = send_notice(title, body)
    if via:
        write_cursor_text(CURSOR, now.strftime("%Y-%m-%d"))
        print(f"[digest] sent via {', '.join(via)}")
    else:
        # No channel took it: try again next run (a channel outage, or none
        # configured). Never recorded as sent.
        print("[digest] no channel delivered it; will retry next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
