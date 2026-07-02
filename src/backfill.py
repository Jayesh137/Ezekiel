# src/backfill.py
"""One-time historical backfill — grabs ALL available data before it expires."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, hl_post, append_records, write_cursor,
    save_latest, update_index, DATA_DIR
)


def backfill_fills(wallet: str) -> int:
    """Backfill all available fills using time-windowed pagination."""
    total = 0
    window_ms = 7 * 24 * 60 * 60 * 1000  # 7 days
    start_time = 0
    max_time = int(time.time() * 1000)

    print("[backfill] Backfilling fills from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userFillsByTime",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        fills = hl_post(body)

        if fills:
            added = append_records(str(DATA_DIR / "fills"), fills, key_field="tid")
            total += added
            print(f"[backfill]   window {start_time} -> {end_time}: {len(fills)} fills ({added} new)")

            if len(fills) >= 2000:
                last_ts = max(f["time"] for f in fills)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.5)

    all_fills = []
    for fp in sorted((DATA_DIR / "fills").glob("*.json")):
        if fp.name == "latest.json":
            continue
        with open(fp) as f:
            all_fills.extend(json.load(f))

    if all_fills:
        max_ts = max(f["time"] for f in all_fills)
        write_cursor("last_fill_time", max_ts)

    return total


def backfill_funding(wallet: str) -> int:
    """Backfill all funding payments using 30-day windows."""
    total = 0
    window_ms = 30 * 24 * 60 * 60 * 1000  # 30 days
    start_time = 0
    max_time = int(time.time() * 1000)

    print("[backfill] Backfilling funding from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userFunding",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        funding = hl_post(body)

        if funding:
            added = append_records(str(DATA_DIR / "funding"), funding, key_field="hash")
            total += added

            if len(funding) >= 500:
                last_ts = max(f["time"] for f in funding)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.5)

    if total > 0:
        all_funding = []
        for fp in sorted((DATA_DIR / "funding").glob("*.json")):
            if fp.name == "latest.json":
                continue
            with open(fp) as f:
                all_funding.extend(json.load(f))
        if all_funding:
            write_cursor("last_funding_time", max(f["time"] for f in all_funding))

    return total


def backfill_ledger(wallet: str) -> int:
    """Backfill all non-funding ledger updates (deposits, withdrawals, transfers)."""
    total = 0
    window_ms = 30 * 24 * 60 * 60 * 1000  # 30 days
    start_time = 0
    max_time = int(time.time() * 1000)

    print("[backfill] Backfilling ledger from epoch to now...")

    while start_time < max_time:
        end_time = min(start_time + window_ms, max_time)
        body = {
            "type": "userNonFundingLedgerUpdates",
            "user": wallet,
            "startTime": start_time,
            "endTime": end_time,
        }
        ledger = hl_post(body)

        if ledger:
            added = append_records(str(DATA_DIR / "ledger"), ledger, key_field="hash")
            total += added

            if len(ledger) >= 500:
                last_ts = max(e["time"] for e in ledger)
                start_time = last_ts + 1
                continue

        start_time = end_time + 1
        time.sleep(0.5)

    if total > 0:
        all_ledger = []
        for fp in sorted((DATA_DIR / "ledger").glob("*.json")):
            if fp.name == "latest.json":
                continue
            with open(fp) as f:
                all_ledger.extend(json.load(f))
        if all_ledger:
            write_cursor("last_ledger_time", max(e["time"] for e in all_ledger))

    return total


def backfill_orders(wallet: str) -> int:
    """Capture historical orders (limited to last 2000)."""
    print("[backfill] Capturing historical orders (last 2000)...")
    historical = hl_post({"type": "historicalOrders", "user": wallet})
    added = append_records(
        str(DATA_DIR / "orders"),
        [{"oid": o["order"]["oid"], **o} for o in historical],
        key_field="oid",
    )
    return added


def backfill_current_state(wallet: str) -> None:
    """Snapshot all current state endpoints."""
    print("[backfill] Capturing current state snapshots...")

    state = hl_post({"type": "clearinghouseState", "user": wallet})
    save_latest(str(DATA_DIR / "positions"), state)

    spot = hl_post({"type": "spotClearinghouseState", "user": wallet})
    save_latest(str(DATA_DIR / "account"), {"perp": state, "spot": spot})

    for endpoint, directory in [
        ("subAccounts", "subaccounts"),
        ("userVaultEquities", "vaults"),
        ("referral", "referral"),
        ("userFees", "fees"),
        ("userRateLimit", "rate_limit"),
        ("portfolio", "account"),
    ]:
        data = hl_post({"type": endpoint, "user": wallet})
        save_latest(str(DATA_DIR / directory), data)
        print(f"[backfill]   {endpoint}: saved")


def main():
    config = load_config()
    wallet = config["target_wallet"]

    print(f"[backfill] === HISTORICAL BACKFILL for {wallet} ===")
    print("[backfill] This captures ALL available data before it expires.")
    print()

    total_fills = backfill_fills(wallet)
    print(f"[backfill] Total fills captured: {total_fills}")
    print()

    total_funding = backfill_funding(wallet)
    print(f"[backfill] Total funding events captured: {total_funding}")
    print()

    total_ledger = backfill_ledger(wallet)
    print(f"[backfill] Total ledger events captured: {total_ledger}")
    print()

    total_orders = backfill_orders(wallet)
    print(f"[backfill] Total historical orders captured: {total_orders}")
    print()

    backfill_current_state(wallet)

    update_index()

    print()
    print("[backfill] === BACKFILL COMPLETE ===")
    print(f"[backfill] Fills: {total_fills}")
    print(f"[backfill] Funding: {total_funding}")
    print(f"[backfill] Ledger: {total_ledger}")
    print(f"[backfill] Orders: {total_orders}")


if __name__ == "__main__":
    main()
