# src/collector.py
"""Collects trading data from Hyperliquid API for the target wallet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    DATA_DIR,
    account_value_components,
    append_records,
    hl_post,
    load_config,
    now_ms,
    read_cursor,
    save_latest,
    save_snapshot,
    update_index,
    write_cursor,
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

    # Publish the composite as latest, not only as a dated snapshot. Without
    # this, account/latest.json was never written by this code at all — it still
    # held a portfolio payload (a LIST) left by an older collector. Both readers
    # do `data.get("perp", data)`, which raises AttributeError on a list, and
    # both swallowed it, so risk.py scored drawdown as 0.0 forever and
    # check_account_value_drop never fired once.
    account_state = {"perp": state, "spot": spot, "hip3": hip3_positions}
    save_snapshot(str(DATA_DIR / "account"), account_state)
    save_latest(str(DATA_DIR / "account"), account_state)


# Hyperliquid returns at most ~2000 records per userFillsByTime call. A single
# unpaginated call therefore silently truncates whenever the gap since the last
# cursor holds more than that — which is exactly what happens after an outage.
# The 21-day collection stall left >2000 fills unrecovered until this loop existed.
HL_PAGE_LIMIT = 2000
# Bound the work per run so a routine 15-minute collection stays fast; a long
# outage is drained over consecutive runs (or in one pass via backfill.py).
MAX_FILL_PAGES = 15


def collect_fills(wallet: str) -> int:
    """Collect new fills since last cursor, paginating through full pages.

    Returns count of new fills added.
    """
    last_ts = read_cursor("last_fill_time")
    start = last_ts + 1 if last_ts else 0
    added_total = 0

    for page in range(MAX_FILL_PAGES):
        fills = hl_post({"type": "userFillsByTime", "user": wallet, "startTime": start})
        if not fills:
            break

        added_total += append_records(str(DATA_DIR / "fills"), fills, key_field="tid")
        max_ts = max(f["time"] for f in fills)
        write_cursor("last_fill_time", max_ts)

        if len(fills) < HL_PAGE_LIMIT:
            break  # caught up
        if max_ts <= start:
            break  # no forward progress — avoid an infinite loop on a stuck page
        start = max_ts + 1
        print(f"[collector] fills page {page + 1} full ({len(fills)}), continuing from {max_ts}")
    else:
        print(f"[collector] fills: stopped at page cap ({MAX_FILL_PAGES}); "
              f"more history remains, next run will continue")

    return added_total


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

    # Hyperliquid reports the ZERO hash on every funding row, so keying the
    # per-file dedupe on `hash` kept one funding payment per file and silently
    # dropped the rest: measured 2026-09-10, 12,132 rows collected over 203
    # days against ~1,200 payments a day for a book this size. (time, coin)
    # identifies a funding payment; the composite is stored so append_records
    # can key on it, and utils.record_key reads the same pair back.
    for f in funding:
        if isinstance(f, dict):
            f["_key"] = f"{f.get('time')}:{(f.get('delta') or {}).get('coin')}"
    added = append_records(str(DATA_DIR / "funding"), funding, key_field="_key")

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
    """Collect fee schedule and rate info.

    Only latest.json carries the full payload (the dashboard reads that). The
    dated history keeps a small summary: appending the whole schedule — including
    the per-coin fee tiers and the 30-day volume table — cost ~5 KB per run and
    had grown data/fees to 14 MB with no reader at all.
    """
    fees = hl_post({"type": "userFees", "user": wallet})
    if not isinstance(fees, dict) or not fees:
        return
    save_latest(str(DATA_DIR / "fees"), fees)
    summary = {
        "_ts": now_ms(),
        "userCrossRate": fees.get("userCrossRate"),
        "userAddRate": fees.get("userAddRate"),
        "activeReferralDiscount": fees.get("activeReferralDiscount"),
    }
    append_records(str(DATA_DIR / "fees"), [summary], key_field="_ts")


def collect_rate_limit(wallet: str) -> None:
    """Collect rate limit / cumulative volume info.

    cumVlm is the only field with a useful time series; the rest is derivable
    from it or static."""
    rl = hl_post({"type": "userRateLimit", "user": wallet})
    if not isinstance(rl, dict) or not rl:
        return
    save_latest(str(DATA_DIR / "rate_limit"), rl)
    append_records(str(DATA_DIR / "rate_limit"),
                   [{"_ts": now_ms(), "cumVlm": rl.get("cumVlm"),
                     "nRequestsUsed": rl.get("nRequestsUsed")}],
                   key_field="_ts")


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


def collect_agents(wallet: str) -> None:
    """Approved API/agent wallets — a DIRECT ownership link.

    An agent is an address the account EXPLICITLY authorised to trade on its
    behalf, so two accounts authorising the same agent are controlled by the
    same person. That makes this one of the strongest signals available, and it
    is worth recording even when the answer is "none".

    This used to `break` on the first truthy response and save nothing
    otherwise. `extraAgents` returns `[]` for an account with no agents — falsy
    — so a real answer of "he has none" was discarded, leaving data/agents/
    empty and indistinguishable from the endpoint being broken. Now every
    endpoint is queried and the outcome recorded either way: `agents: []` means
    we asked and he has none, and a later non-empty list is a NEW address he
    controls.
    """
    out = {"wallet": wallet.lower(), "agents": [], "sources": {}, "errors": []}
    for req_type in ("extraAgents", "userToMultiSigSigners"):
        try:
            resp = hl_post({"type": req_type, "user": wallet})
        except Exception as exc:                      # noqa: BLE001 - transport
            out["errors"].append(f"{req_type}: {type(exc).__name__}: {exc}")
            continue
        out["sources"][req_type] = resp
        for entry in resp if isinstance(resp, list) else []:
            addr = (entry.get("address") if isinstance(entry, dict) else entry)
            if isinstance(addr, str) and addr.startswith("0x"):
                out["agents"].append({
                    "address": addr.lower(),
                    "name": entry.get("name") if isinstance(entry, dict) else None,
                    "valid_until": entry.get("validUntil") if isinstance(entry, dict) else None,
                    "source": req_type,
                })
    # `extraAgents` lists only NAMED extra agents. The frontend's own agent —
    # the one that signs his orders — appears only in webData2. Measured
    # 2026-09-10: extraAgents said [] while webData2 showed an agent approved
    # four days earlier, so the "he has no agents" baseline was false.
    try:
        from src.hl_identity import parse_web_data
        web = parse_web_data(hl_post({"type": "webData2", "user": wallet}))
        out["sources"]["webData2"] = {"agentAddress": web["agent_address"],
                                      "agentValidUntil": web["agent_valid_until"]}
        if web["agent_address"]:
            out["agents"].append({"address": web["agent_address"], "name": None,
                                  "valid_until": web["agent_valid_until"],
                                  "source": "webData2"})
    except Exception as exc:                          # noqa: BLE001 - transport
        out["errors"].append(f"webData2: {type(exc).__name__}: {exc}")
    save_latest(str(DATA_DIR / "agents"), out)


def collect_portfolio(wallet: str) -> None:
    """Collect portfolio (historical account value + PnL)."""
    portfolio = hl_post({"type": "portfolio", "user": wallet})
    save_latest(str(DATA_DIR / "portfolio"), portfolio)


def collect_actions() -> None:
    """The cluster's own non-trading actions, from the Hyperliquid explorer.

    The ledger says money moved; the explorer says where a withdrawal went,
    which agent was approved, whether a sub-account was created. Its window is
    the last 300 actions and cannot be paged — a few hours on a heavy day — so
    it is read on every collection run and everything non-trading is kept.
    A withdrawal, send or approval to an address outside the cluster alerts.
    """
    import time as _time

    from src.alerts import alert_foreign_destination
    from src.hl_actions import (
        ACTIONS_DIR,
        fetch_actions,
        foreign_destinations,
        own_actions,
        record,
        save,
        summarise,
    )
    from src.utils import load_all_records

    config = load_config()
    cluster = {(config.get("target_wallet") or "").lower()}
    cluster |= {(w or "").lower() for w in config.get("known_self_wallets", [])}
    # Destinations that receive from everyone and identify nobody.
    ignore = {(a or "").lower() for a in config.get("excluded_addresses", [])}
    ignore |= {(a or "").lower() for a in config.get("hl_shared_destinations", [])}

    reports = {}
    for wallet in sorted(cluster):
        rows, error = fetch_actions(wallet)
        acts = own_actions(rows, wallet) if not error else []
        already = {r.get("_key") for r in load_all_records(str(ACTIONS_DIR / wallet))}
        fresh = [a for a in acts if a.get("hash") and a["hash"] not in already]
        foreign = foreign_destinations(fresh, cluster, ignore)
        for f in foreign:
            when = None
            try:
                from datetime import UTC, datetime
                when = datetime.fromtimestamp(int(f["time"]) / 1000, tz=UTC).isoformat()
            except (TypeError, ValueError):
                pass
            alert_foreign_destination(wallet, f["type"], f["destination"],
                                      f.get("amount"), f.get("token"), when, f.get("hash"))
        added = record(wallet, acts)
        reports[wallet] = summarise(wallet, acts, foreign_destinations(acts, cluster, ignore), error)
        reports[wallet]["new_this_run"] = added
        if error:
            print(f"[collector] actions for {wallet[:12]}... UNREADABLE: {error}")
        else:
            print(f"[collector] actions for {wallet[:12]}...: {len(acts)} non-trading in window, "
                  f"{added} new, {len(foreign)} new foreign destination(s)")
        _time.sleep(0.2)
    save({"computed_at": now_ms(), "wallets": reports})


def analyze_and_alert_hl_transfers() -> None:
    """Rebuild the HL-native transfer counterparty map from the freshly-collected
    ledger and alert on any new large outbound transfer to an unknown wallet."""
    from src.ledger_analyzer import analyze_hl_transfers, check_new_outbound_transfers
    result = analyze_hl_transfers()
    print(f"[collector] HL-native counterparties: {result['counterparty_count']}")
    alerted = check_new_outbound_transfers(result)
    if alerted:
        print(f"[collector] {len(alerted)} new HL-native outbound transfer alert(s)")


def compute_migration_risk() -> None:
    """Recompute the unified migration risk score from all current signals."""
    from src.risk import run_risk
    run_risk()


def check_own_freshness() -> None:
    """Self-report a collection stall from inside the collector.

    The heartbeat workflow is triggered by the same GitHub scheduler it supervises,
    and that scheduler drops ~95% of high-frequency runs here. This inline check
    means a collector that IS running will report a preceding gap even if the
    heartbeat workflow never fired. It cannot detect a total scheduling outage —
    nothing inside Actions can.
    """
    from src.heartbeat import STALE_AFTER_MINUTES, data_age_minutes, is_stale

    age = data_age_minutes()
    if age is None:
        return  # first ever run, or no index yet
    if is_stale(age, STALE_AFTER_MINUTES):
        print(f"[collector] NOTE: {age:.0f} min since the last recorded update "
              f"(threshold {STALE_AFTER_MINUTES}) — collection had stalled before "
              f"this run; catching up now.")


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
        if not isinstance(latest, dict):
            raise TypeError(
                f"expected an object with a 'perp' key, got {type(latest).__name__}")
        # The WHOLE account, not just the perp margin summary. Reading perp
        # alone called an internal perp -> spot transfer a 52% liquidation on
        # 2026-09-11 while the total was flat.
        components = account_value_components(latest)
        current_value = components["total"]
        if current_value is None:
            raise ValueError("no readable account value")
    except Exception as e:
        # Returning quietly here is how this alert stayed dead: the file held a
        # stale portfolio payload and every run bailed out before the comparison.
        print(f"[collector] WARNING: cannot check account-value drop, "
              f"{latest_path} is unreadable: {e}")
        return

    if current_value <= 0:
        return

    current_cents = int(current_value * 100)
    prev_cents = read_cursor("prev_account_value_cents")

    # True high-water mark, ratcheted upward only and never reset by an alert.
    # The drop-alert cursor below IS reset on fire (so we don't re-alert hourly on
    # the same collapse), and risk.py previously read that same cursor as its
    # high-water — which zeroed drawdown_pct at the exact moment it mattered.
    hw_cents = read_cursor("account_high_water_cents")
    if current_cents > (hw_cents or 0):
        write_cursor("account_high_water_cents", current_cents)

    if prev_cents and prev_cents > 1_000_000:  # Only check if previous reading was > $10k
        prev_value = prev_cents / 100.0
        drop_pct = (prev_value - current_value) / prev_value
        if drop_pct > 0.40:
            last_alert = read_cursor("last_drop_alert")
            if not last_alert or (now_ms() - last_alert) > 60 * 60 * 1000:
                from src.alerts import alert_account_value_drop
                if alert_account_value_drop(current_value, prev_value, drop_pct,
                                            components):
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
        # Runs first: reports the gap BEFORE this run overwrites the index.
        ("freshness self-check", check_own_freshness),
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
        ("agents", lambda: collect_agents(wallet)),
        ("portfolio", lambda: collect_portfolio(wallet)),
        ("actions", collect_actions),
        ("hl transfer analysis", analyze_and_alert_hl_transfers),
        ("silence check", check_silence),
        ("account drop check", check_account_value_drop),
        ("migration risk", compute_migration_risk),
        ("index", update_index),
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
