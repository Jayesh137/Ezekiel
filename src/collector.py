# src/collector.py
"""Collects trading data from Hyperliquid API for the target wallet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, hl_post, read_cursor, write_cursor,
    append_records, save_snapshot, save_latest, update_index,
    now_ms, DATA_DIR
)


def collect_positions(wallet: str) -> None:
    """Snapshot current positions and account state."""
    state = hl_post({"type": "clearinghouseState", "user": wallet})
    save_snapshot(str(DATA_DIR / "positions"), state)
    save_latest(str(DATA_DIR / "positions"), state)

    spot = hl_post({"type": "spotClearinghouseState", "user": wallet})
    save_latest(str(DATA_DIR / "spot"), spot)

    save_snapshot(str(DATA_DIR / "account"), {"perp": state, "spot": spot})


def collect_fills(wallet: str) -> int:
    """Collect new fills since last cursor. Returns count of new fills."""
    last_ts = read_cursor("last_fill_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userFillsByTime", "user": wallet, "startTime": start}
    fills = hl_post(body)

    if not fills:
        return 0

    added = append_records(str(DATA_DIR / "fills"), fills, key_field="hash")

    max_ts = max(f["time"] for f in fills)
    write_cursor("last_fill_time", max_ts)

    return added


def collect_orders(wallet: str) -> None:
    """Collect open orders and recent historical orders."""
    open_orders = hl_post({"type": "openOrders", "user": wallet})
    save_latest(str(DATA_DIR / "orders"), {"open": open_orders})

    frontend_orders = hl_post({"type": "frontendOpenOrders", "user": wallet})
    save_snapshot(str(DATA_DIR / "orders"), {
        "open": open_orders,
        "frontend": frontend_orders,
    })

    historical = hl_post({"type": "historicalOrders", "user": wallet})
    append_records(
        str(DATA_DIR / "orders"),
        [{"oid": o["order"]["oid"], **o} for o in historical],
        key_field="oid",
    )


def collect_funding(wallet: str) -> int:
    """Collect new funding payments since last cursor."""
    last_ts = read_cursor("last_funding_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userFunding", "user": wallet, "startTime": start}
    funding = hl_post(body)

    if not funding:
        return 0

    added = append_records(str(DATA_DIR / "funding"), funding, key_field="hash")

    max_ts = max(f["time"] for f in funding)
    write_cursor("last_funding_time", max_ts)

    return added


def collect_ledger(wallet: str) -> int:
    """Collect non-funding ledger updates (deposits, withdrawals, transfers)."""
    last_ts = read_cursor("last_ledger_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userNonFundingLedgerUpdates", "user": wallet, "startTime": start}
    ledger = hl_post(body)

    if not ledger:
        return 0

    added = append_records(str(DATA_DIR / "ledger"), ledger, key_field="hash")

    max_ts = max(e["time"] for e in ledger)
    write_cursor("last_ledger_time", max_ts)

    return added


def collect_fees(wallet: str) -> None:
    """Collect fee schedule and rate info."""
    fees = hl_post({"type": "userFees", "user": wallet})
    save_latest(str(DATA_DIR / "fees"), fees)
    append_records(str(DATA_DIR / "fees"), [{"_ts": now_ms(), **fees}], key_field="_ts")


def collect_rate_limit(wallet: str) -> None:
    """Collect rate limit / cumulative volume info."""
    rl = hl_post({"type": "userRateLimit", "user": wallet})
    save_latest(str(DATA_DIR / "rate_limit"), rl)
    append_records(str(DATA_DIR / "rate_limit"), [{"_ts": now_ms(), **rl}], key_field="_ts")


def collect_subaccounts(wallet: str) -> None:
    """Check for subaccounts (directly reveals linked wallets)."""
    subs = hl_post({"type": "subAccounts", "user": wallet})
    save_latest(str(DATA_DIR / "subaccounts"), subs)


def collect_vault_equities(wallet: str) -> None:
    """Check vault deposits."""
    vaults = hl_post({"type": "userVaultEquities", "user": wallet})
    save_latest(str(DATA_DIR / "vaults"), vaults)


def collect_referral(wallet: str) -> None:
    """Collect referral chain data."""
    ref = hl_post({"type": "referral", "user": wallet})
    save_latest(str(DATA_DIR / "referral"), ref)


def collect_portfolio(wallet: str) -> None:
    """Collect portfolio (historical account value + PnL)."""
    portfolio = hl_post({"type": "portfolio", "user": wallet})
    save_latest(str(DATA_DIR / "portfolio"), portfolio)


def main():
    config = load_config()
    wallet = config["target_wallet"]

    print(f"[collector] Starting collection for {wallet}")

    print("[collector] Collecting positions...")
    collect_positions(wallet)

    print("[collector] Collecting fills...")
    new_fills = collect_fills(wallet)
    print(f"[collector] {new_fills} new fills")

    print("[collector] Collecting orders...")
    collect_orders(wallet)

    print("[collector] Collecting funding...")
    new_funding = collect_funding(wallet)
    print(f"[collector] {new_funding} new funding events")

    print("[collector] Collecting ledger...")
    new_ledger = collect_ledger(wallet)
    print(f"[collector] {new_ledger} new ledger events")

    print("[collector] Collecting fees...")
    collect_fees(wallet)

    print("[collector] Collecting rate limit...")
    collect_rate_limit(wallet)

    print("[collector] Collecting subaccounts...")
    collect_subaccounts(wallet)

    print("[collector] Collecting vault equities...")
    collect_vault_equities(wallet)

    print("[collector] Collecting referral data...")
    collect_referral(wallet)

    print("[collector] Collecting portfolio...")
    collect_portfolio(wallet)

    print("[collector] Updating index...")
    update_index()

    print("[collector] Collection complete.")


if __name__ == "__main__":
    main()
