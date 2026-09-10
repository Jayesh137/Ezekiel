#!/usr/bin/env python3
"""Watch the cluster's Solana addresses for any new transaction."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_solana_activity
from src.solana_watch import WATCH_DIR, build_report, fetch_signatures, load_addresses, save


def _previous() -> dict:
    try:
        with open(WATCH_DIR / "latest.json") as f:
            return {w["address"]: w.get("last_signature")
                    for w in json.load(f).get("wallets", []) if w.get("address")}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def main() -> int:
    addresses = load_addresses()
    if not addresses:
        print("[solana] no addresses on record — nothing to watch")
        return 0
    readings = {}
    for addr in addresses:
        readings[addr] = fetch_signatures(addr)
        time.sleep(0.3)
    report = build_report(addresses, readings, _previous())
    save(report)
    for w in report["wallets"]:
        if not w["read_ok"]:
            print(f"[solana] {w['address']} UNREADABLE: {w['error']} — not an all-clear")
            continue
        print(f"[solana] {w['address']} last activity {w['last_activity']}, "
              f"{len(w['new_signatures'])} new signature(s)"
              + (" (first run: baseline recorded, no alert)" if w["first_run"] else ""))
        if w["new_signatures"] and not w["first_run"]:
            alert_solana_activity(w["address"], w["new_signatures"], w["last_activity"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
