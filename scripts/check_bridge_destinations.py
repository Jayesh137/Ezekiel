#!/usr/bin/env python3
"""Where every bridge transfer from the cluster actually went.

The calldata of a CCTP or Socket transaction names the destination chain and
recipient, and Hyperliquid's CCTP extension names the HyperCore account in
its hook data. Decoded 2026-09-10 for the target: $66M was him depositing into
his own Hyperliquid account through Circle, $22.75M went to a Solana wallet
of his, and the rest to himself on Ethereum. A future transfer whose
recipient is not a cluster address is the tripwire.

Blockscout, no key; each transaction decoded once and cached forever.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_foreign_destination
from src.chain.bridges import CACHE_NAME, DecodeCache, decode_transfers, summarise
from src.chain.collect import records_for
from src.chain.labels import load_registry, service_addresses
from src.utils import DATA_DIR, load_config, save_latest

BRIDGE_DIR = DATA_DIR / "bridge_destinations"
MAX_LOOKUPS = 25


def main() -> int:
    config = load_config()
    cluster = {(config.get("target_wallet") or "").lower()}
    cluster |= {(w or "").lower() for w in config.get("known_self_wallets", [])}
    registry = load_registry(DATA_DIR / "labels" / "entities.json")
    bridges = service_addresses(registry, categories={"bridge"})
    bridges.discard((config.get("hl_bridge_contract") or "").lower())  # plain deposits

    records = []
    for w in sorted(cluster):
        records.extend(records_for(w))
    cache = DecodeCache(DATA_DIR / "labels" / CACHE_NAME)
    rows, spent = decode_transfers(records, bridges, cluster, cache,
                                   max_lookups=MAX_LOOKUPS, sleep=time.sleep)
    report = summarise(rows)
    report["lookups_spent"] = spent
    report["bridges_watched"] = sorted(bridges)
    save_latest(str(BRIDGE_DIR), report)

    print(f"[bridges] {report['transfers']} bridge transfer(s) from the cluster: "
          f"{report['decoded']} decoded, {report['pending']} pending, "
          f"{report['unreadable']} unreadable, {report['undecoded']} undecodable")
    for d in report["destinations"][:8]:
        print(f"[bridges]   {d['chain']:<9} {d['address']} ${d['usd']:,.0f} "
              f"x{d['transfers']} {'FOREIGN' if d['foreign'] else 'self'}")
    for f in report["foreign"]:
        print(f"[bridges] FOREIGN {f['protocol']} -> {f['destination_chain']} "
              f"{f.get('hl_account') or f.get('recipient')} ${f.get('amount_usd') or 0:,.0f}")
        alert_foreign_destination(
            f["src"], f"bridge:{f['protocol']}->{f['destination_chain']}",
            f.get("hl_account") or f.get("recipient") or "", f.get("amount_usd"), "USD",
            None, f.get("tx_hash"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
