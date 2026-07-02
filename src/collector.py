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
    config = load_config()

    state = hl_post({"type": "clearinghouseState", "user": wallet})
    save_snapshot(str(DATA_DIR / "positions"), state)
    save_latest(str(DATA_DIR / "positions"), state)

    spot = hl_post({"type": "spotClearinghouseState", "user": wallet})
    save_snapshot(str(DATA_DIR / "spot"), spot)
    save_latest(str(DATA_DIR / "spot"), spot)

    # Fetch HIP-3 dex positions (e.g. xyz:XYZ100, xyz:SILVER)
    hip3_positions = {}
    for dex in config.get("hip3_dexes", []):
        dex_state = hl_post({"type": "clearinghouseState", "user": wallet, "dex": dex})
        if dex_state:
            hip3_positions[dex] = dex_state
            save_snapshot(str(DATA_DIR / f"positions_hip3_{dex}"), dex_state)
            save_latest(str(DATA_DIR / f"positions_hip3_{dex}"), dex_state)

    save_snapshot(str(DATA_DIR / "account"), {"perp": state, "spot": spot, "hip3": hip3_positions})


def collect_fills(wallet: str) -> int:
    """Collect new fills since last cursor. Returns count of new fills."""
    last_ts = read_cursor("last_fill_time")
    start = last_ts + 1 if last_ts else 0

    body = {"type": "userFillsByTime", "user": wallet, "startTime": start}
    fills = hl_post(body)

    if not fills:
        return 0

    added = append_records(str(DATA_DIR / "fills"), fills, key_field="tid")

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
    if not isinstance(fees, dict) or not fees:
        return
    save_latest(str(DATA_DIR / "fees"), fees)
    append_records(str(DATA_DIR / "fees"), [{"_ts": now_ms(), **fees}], key_field="_ts")


def collect_rate_limit(wallet: str) -> None:
    """Collect rate limit / cumulative volume info."""
    rl = hl_post({"type": "userRateLimit", "user": wallet})
    if not isinstance(rl, dict) or not rl:
        return
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


def check_silence() -> None:
    """Alert if the target has not traded in 3+ days. Cooldown: once per 24h."""
    last_fill_ts = read_cursor("last_fill_time")
    if not last_fill_ts:
        return

    days_silent = (now_ms() - last_fill_ts) / (24 * 60 * 60 * 1000)
    if days_silent < 3:
        return

    last_alert = read_cursor("last_silence_alert")
    if last_alert and (now_ms() - last_alert) < 24 * 60 * 60 * 1000:
        return

    from src.alerts import alert_target_silence
    if alert_target_silence(round(days_silent, 1)):
        write_cursor("last_silence_alert", now_ms())


def check_account_value_drop() -> None:
    """Alert if account value dropped >40% since last collection. Cooldown: once per 1h."""
    import json as _json

    latest_path = DATA_DIR / "account" / "latest.json"
    if not latest_path.exists():
        return

    try:
        with open(latest_path) as f:
            latest = _json.load(f)
        perp = latest.get("perp", latest) or {}
        ms_data = perp.get("marginSummary", {}) or {}
        current_value = float(ms_data.get("accountValue", 0))
    except Exception:
        return

    if current_value <= 0:
        return

    current_cents = int(current_value * 100)
    prev_cents = read_cursor("prev_account_value_cents")

    if prev_cents and prev_cents > 1_000_000:  # Only check if previous reading was > $10k
        prev_value = prev_cents / 100.0
        drop_pct = (prev_value - current_value) / prev_value
        if drop_pct > 0.40:
            last_alert = read_cursor("last_drop_alert")
            if not last_alert or (now_ms() - last_alert) > 60 * 60 * 1000:
                from src.alerts import alert_account_value_drop
                if alert_account_value_drop(current_value, prev_value, drop_pct):
                    write_cursor("last_drop_alert", now_ms())
                    # Reset high-water mark so we don't re-alert hourly on the same drop;
                    # a further 40% drop from here will still trigger.
                    write_cursor("prev_account_value_cents", current_cents)
                    return

    # Track high-water mark: only update upward so transient dips still trigger the alert
    if not prev_cents or current_cents > prev_cents:
        write_cursor("prev_account_value_cents", current_cents)


def main():
    config = load_config()
    wallet = config["target_wallet"]

    print(f"[collector] Starting collection for {wallet}")

    # Each step runs independently — one failed API response must not kill the run.
    steps = [
        ("positions", lambda: collect_positions(wallet)),
        ("fills", lambda: print(f"[collector] {collect_fills(wallet)} new fills")),
        ("orders", lambda: collect_orders(wallet)),
        ("funding", lambda: print(f"[collector] {collect_funding(wallet)} new funding events")),
        ("ledger", lambda: print(f"[collector] {collect_ledger(wallet)} new ledger events")),
        ("fees", lambda: collect_fees(wallet)),
        ("rate limit", lambda: collect_rate_limit(wallet)),
        ("subaccounts", lambda: collect_subaccounts(wallet)),
        ("vault equities", lambda: collect_vault_equities(wallet)),
        ("referral", lambda: collect_referral(wallet)),
        ("portfolio", lambda: collect_portfolio(wallet)),
        ("index", update_index),
        ("silence check", check_silence),
        ("account drop check", check_account_value_drop),
    ]

    failed = []
    for name, step in steps:
        print(f"[collector] Collecting {name}...")
        try:
            step()
        except Exception as e:
            failed.append(name)
            print(f"[collector] WARNING: {name} failed: {e}")

    if failed:
        print(f"[collector] Collection finished with {len(failed)} failed step(s): {', '.join(failed)}")
    else:
        print("[collector] Collection complete.")


if __name__ == "__main__":
    main()
