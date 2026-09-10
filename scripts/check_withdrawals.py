#!/usr/bin/env python3
"""Pair every Hyperliquid withdrawal with the bridge payout that landed at the
target's own address; resolve the destination of any that did not.

Measured 2026-09-10: 146 of 146 matched. The unmatched case is a withdrawal
to a foreign Arbitrum address, which the ledger cannot show and the tracer
never sweeps.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_foreign_destination
from src.chain.collect import records_for
from src.utils import DATA_DIR, load_all_records, load_config
from src.withdrawals import (
    bridge_inbound,
    build_report,
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


def main() -> int:
    config = load_config()
    target = (config.get("target_wallet") or "").lower()
    bridge = (config.get("hl_bridge_contract") or "").lower()

    withdrawals = unique_withdrawals(load_all_records(str(DATA_DIR / "ledger")))
    inbound = bridge_inbound(records_for(target), bridge, target)
    m = match(withdrawals, inbound)
    resolved = resolve_destinations(m["unmatched"], _window_fetcher(config), bridge)
    report = build_report(withdrawals, inbound, resolved)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
