#!/usr/bin/env python3
"""Pair every Hyperliquid withdrawal with the bridge payout that landed at the
target's own address; resolve the destination of any that did not.

Measured 2026-09-10: 146 of 146 matched. The unmatched case is a withdrawal
to a foreign Arbitrum address, which the ledger cannot show and the tracer
never sweeps.

Since 2026-09-12 the same is done for the Circle route: every spot send to
the USDC system address is paired with the mint from the zero address at a
cluster address on any chain the substrate reads (measured: 6 of 6, $30M,
all at his own Arbitrum address), the rest are resolved from the stored
`sendToEvmWithData` payloads, and what neither can name is alerted.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_foreign_destination, alert_unresolved_cctp_withdrawal
from src.chain.collect import records_for
from src.hl_actions import ACTIONS_DIR
from src.utils import DATA_DIR, load_all_records, load_config
from src.withdrawals import (
    bridge_inbound,
    build_report,
    cctp_inbound,
    cctp_lines,
    cctp_report,
    cctp_withdrawals,
    match,
    resolve_destinations,
    save,
    unique_withdrawals,
)


def _window_fetcher(config: dict):
    """Bridge USDC transfers from two minutes before a time, a few pages on."""
    from src.chain.budget import CallBudget
    from src.chain.chains import chain_by_name
    from src.chain.client import block_at_time, fetch_transfers_to

    if not os.environ.get("ETHERSCAN_API_KEY"):
        return lambda t: ([], "skipped_no_api_key")
    chain = chain_by_name("arbitrum", config)
    budget = CallBudget(max_calls=25, seconds=60)

    def fetch(time_s: int):
        start, err = block_at_time(chain, int(time_s) - 120, budget)
        if start is None:
            return [], f"could not resolve block: {err}"
        walk, err = fetch_transfers_to(config["hl_bridge_contract"], chain,
                                       config["usdc_contract_arbitrum"], start, budget,
                                       max_pages=4)
        return walk.rows, err
    return fetch


def cctp_payloads(target: str) -> dict:
    """hash -> the stored `sendToEvmWithData` payload, as the collector keeps it."""
    out = {}
    for a in load_all_records(str(ACTIONS_DIR / target)):
        if a.get("type") == "sendToEvmWithData" and a.get("hash"):
            out[a["hash"]] = a
    return out


def circle_report(config: dict, ledger: list[dict], target: str) -> dict:
    """Pair every Circle withdrawal with its mint at a cluster address, on any
    chain the substrate reads; resolve the rest from the stored payloads."""
    cluster = {target} | {(w or "").lower() for w in config.get("known_self_wallets", []) or []}
    inbound = []
    for w in sorted(cluster):
        inbound.extend(cctp_inbound(records_for(w), {w}))
    inbound.sort(key=lambda r: r["ts"])
    return cctp_report(cctp_withdrawals(ledger, target), inbound, cctp_payloads(target), cluster)


def _iso(time_s) -> str | None:
    try:
        from datetime import UTC, datetime
        return datetime.fromtimestamp(int(time_s), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def main() -> int:
    config = load_config()
    target = (config.get("target_wallet") or "").lower()
    bridge = (config.get("hl_bridge_contract") or "").lower()

    ledger = load_all_records(str(DATA_DIR / "ledger"))
    withdrawals = unique_withdrawals(ledger)
    inbound = bridge_inbound(records_for(target), bridge, target)
    m = match(withdrawals, inbound)
    resolved = resolve_destinations(m["unmatched"], _window_fetcher(config), bridge)
    report = build_report(withdrawals, inbound, resolved)
    report["cctp"] = circle_report(config, ledger, target)
    save(report)

    print(f"[withdrawals] {report['withdrawals']} withdrawal(s), "
          f"${report['withdrawn_usd']:,.0f}: {report['matched_to_own_address']} landed at "
          f"his own address, {report['settling']} settling, {report['unmatched']} unmatched "
          f"(${report['unmatched_usd']:,.0f})")
    for r in report["foreign"]:
        print(f"[withdrawals] FOREIGN ${r['net_usd']:,.2f} -> {r['destination']} "
              f"({r['delay_s']}s after the withdrawal)")
        alert_foreign_destination(target, "withdraw3", r["destination"], r["net_usd"],
                                  "USDC", None, r.get("tx_hash"))
    for r in report["unresolved"]:
        print(f"[withdrawals] UNRESOLVED ${r['net_usd']:,.2f} at {r['time_s']}: {r['status']}")

    for line in cctp_lines(report["cctp"]):
        print(line)
    for f in report["cctp"]["foreign"]:
        alert_foreign_destination(target, "sendToEvmWithData", f["destination"], f["net_usd"],
                                  "USDC", _iso(f.get("time_s")), f.get("hash"),
                                  chain=f.get("chain"))
    for u in report["cctp"]["unresolved"]:
        alert_unresolved_cctp_withdrawal(target, u["net_usd"], _iso(u.get("time_s")),
                                         u.get("hash"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
