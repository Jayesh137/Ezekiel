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

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_foreign_destination
from src.chain.bridges import CACHE_NAME, DecodeCache, decode_transfers, summarise
from src.chain.collect import records_for
from src.chain.labels import load_registry, service_addresses
from src.solana_watch import load_addresses
from src.utils import DATA_DIR, load_config, save_latest

BRIDGE_DIR = DATA_DIR / "bridge_destinations"
MAX_LOOKUPS = 25


def solana_cluster_hexes(addresses: dict | None = None) -> set:
    """Known Solana cluster addresses, as the 32-byte hex a CCTP burn names.

    A burn to Solana carries a 32-byte mint recipient, which can never equal
    an EVM address, so without these every one of his twenty-three burns to
    his own Solana wallet reads as "outside the cluster" and alerts forever.
    """
    addresses = addresses if addresses is not None else (load_addresses() or {})
    return {h for rec in addresses.values()
            if rec.get("role") == "cluster"
            and (h := (rec.get("mint_recipient_hex") or "").lower())}


def decoded_wallets(config: dict, roster: dict | None) -> tuple[list, list]:
    """(config cluster, roster CONFIRMED wallets) whose bridging is decoded.

    Config alone left `0xf078969e…` — CONFIRMED, two-way with the target at
    $135M/$148M — with nothing reading its bridge calldata, while it sent $32.8M
    through Circle to Monad in the month to 2026-09-15. A CctpExtension deposit
    names the Hyperliquid account it credits; from this wallet, nobody looked.
    Watching a CONFIRMED wallet is a question, like the close watch: it never
    joins `known_self_wallets` or any immunity.
    """
    cluster = [(config.get("target_wallet") or "").lower()]
    cluster += [(w or "").lower() for w in config.get("known_self_wallets") or []]
    cluster = sorted({c for c in cluster if c})
    confirmed = sorted({(r.get("wallet") or "").lower() for r in (roster or {}).get("wallets") or []
                        if isinstance(r, dict) and r.get("tier") == "CONFIRMED"
                        and not r.get("is_service")} - set(cluster) - {""})
    return cluster, confirmed


def foreign_key(row: dict) -> str:
    return f"{(row.get('src') or '').lower()}:{(row.get('tx_hash') or '').lower()}"


def foreign_to_alert(previous: dict | None, report: dict, cluster: list) -> tuple[list, list]:
    """(alert now, baseline) among this run's foreign rows. Pure.

    Every historical foreign row used to re-alert CRITICAL whenever its 72h
    cooldown lapsed — an alert delivered once is not news the fourth time. A
    row is news once: when it first appears for a wallet that was ALREADY being
    decoded. A wallet newly added to the decoded set brings its history as a
    baseline. Undelivered rows from the previous run are retried.
    """
    previous = previous or {}
    before = set(previous.get("decoded_wallets") or cluster)
    seen = {foreign_key(r) for r in previous.get("foreign") or []}
    retry = {foreign_key(r) for r in previous.get("undelivered_foreign") or []}
    alert, baseline = [], []
    for row in report.get("foreign") or []:
        key = foreign_key(row)
        if key in retry:
            alert.append(row)
        elif key in seen:
            continue
        elif (row.get("src") or "").lower() in before:
            alert.append(row)
        else:
            baseline.append(row)
    return alert, baseline


def describe(destination: dict) -> str:
    """One summary line for a decoded destination.

    Every field is optional in practice and this is the only place that
    formats them. A Socket route's destination chain is not in the calldata
    the decoder reads, so `chain` is legitimately None there — formatting it
    with a width raised TypeError and failed the whole trace job on the first
    CI run, after the step had already done its work.
    """
    chain = destination.get("chain") or "unknown"
    address = destination.get("address") or "(no recipient)"
    usd = destination.get("usd") or 0
    transfers = destination.get("transfers") or 0
    tag = "FOREIGN" if destination.get("foreign") else "self"
    return f"{chain:<9} {address} ${usd:,.0f} x{transfers} {tag}"


def _load(path: Path) -> dict:
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    config = load_config()
    previous = _load(BRIDGE_DIR / "latest.json") or None
    cluster, confirmed = decoded_wallets(config, _load(DATA_DIR / "roster" / "latest.json"))
    wallets = cluster + confirmed
    # Only EVM addresses have substrate records to read; the non-EVM ones
    # below exist purely so a destination we already know is his is not
    # reported as foreign.
    known_his = set(wallets) | solana_cluster_hexes()
    registry = load_registry(DATA_DIR / "labels" / "entities.json")
    bridges = service_addresses(registry, categories={"bridge"})
    bridges.discard((config.get("hl_bridge_contract") or "").lower())  # plain deposits

    records = []
    for w in wallets:
        records.extend(records_for(w))
    cache = DecodeCache(DATA_DIR / "labels" / CACHE_NAME)
    rows, spent = decode_transfers(records, bridges, known_his, cache,
                                   max_lookups=MAX_LOOKUPS, sleep=time.sleep)
    report = summarise(rows)
    report["lookups_spent"] = spent
    report["bridges_watched"] = sorted(bridges)
    report["decoded_wallets"] = wallets

    print(f"[bridges] {report['transfers']} bridge transfer(s) from the cluster: "
          f"{report['decoded']} decoded, {report['pending']} pending, "
          f"{report['unreadable']} unreadable, {report['undecoded']} undecodable")
    for d in report["destinations"][:8]:
        print(f"[bridges]   {describe(d)}")
    alert, baseline = foreign_to_alert(previous, report, cluster)
    for f in baseline:
        print(f"[bridges] baseline (wallet newly decoded) {f['src'][:12]}... "
              f"{f['protocol']} -> {f['destination_chain']} "
              f"{f.get('hl_account') or f.get('recipient')} ${f.get('amount_usd') or 0:,.0f}")
    undelivered = []
    for f in alert:
        print(f"[bridges] FOREIGN {f['protocol']} -> {f['destination_chain']} "
              f"{f.get('hl_account') or f.get('recipient')} ${f.get('amount_usd') or 0:,.0f}")
        if not alert_foreign_destination(
                f["src"], f"bridge:{f['protocol']}->{f['destination_chain']}",
                f.get("hl_account") or f.get("recipient") or "", f.get("amount_usd"), "USD",
                None, f.get("tx_hash")):
            undelivered.append(f)
    report["undelivered_foreign"] = undelivered
    save_latest(str(BRIDGE_DIR), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
