#!/usr/bin/env python3
"""Measure the whole-chain activity of every roster CANDIDATE.

Why this exists: `roster.services_from_activity` can only disqualify an
exchange or a contract that has been MEASURED, and measurement was a
side-effect of the transfer-graph walk, which spends its budget on the
highest-VALUE unmeasured addresses. Candidates are a different set. Measured
2026-09-22 on the live roster: of 35 wallets the phone app was showing as
Confirmed/Possible, **18 had never been measured on any chain**, and of the 17
that had, five were exchange-scale and three were named contracts — a Merkle
distributor, a BoringSolver and a Gnosis Safe proxy, each presented to the
operator as a wallet that might be him.

So the candidate list gets its own small budget, spent oldest-first on the
wallets nobody has measured. It is bounded twice (calls and wall clock) for the
reason `ActivityCache` documents, and it writes only the shared cache — the
roster reads it on the next build, so this script decides nothing by itself.

Blockscout serves arbitrum/ethereum/base/optimism/polygon with no key, so this
costs no API quota.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.chain.activity import ActivityCache, is_busy  # noqa: E402
from src.roster import load_roster  # noqa: E402
from src.utils import DATA_DIR, load_config  # noqa: E402

# The chains a person's wallet would show up on, cheapest first. Arbitrum is
# where the target lives, so an exchange or contract is usually visible there.
CHAINS = ("arbitrum", "ethereum")
CANDIDATE_TIERS = {"CONFIRMED", "PROBABLE", "POSSIBLE"}


def candidates(roster: dict, config: dict) -> list[str]:
    """Roster candidates, strongest first, minus config ground truth.

    Ground truth is never measured: the operator's own wallets cannot be
    disqualified by a reading, so spending a lookup on them buys nothing.
    """
    ground = {(config.get("target_wallet") or "").lower()}
    ground |= {(w or "").lower() for w in config.get("known_self_wallets", [])}
    out = []
    for row in roster.get("wallets") or []:
        if not isinstance(row, dict) or row.get("is_service"):
            continue
        if row.get("tier") not in CANDIDATE_TIERS:
            continue
        address = (row.get("wallet") or "").strip().lower()
        if address and address not in ground and address not in out:
            out.append(address)
    return out


def unmeasured(addresses: list[str], table: dict) -> list[str]:
    """Candidates with no reading on ANY chain. A partial reading counts as
    measured: one chain saying 'exchange' is already enough to disqualify, and
    the cache refreshes stale entries on its own."""
    seen = {str(k).split(":", 1)[-1].lower() for k in (table or {})}
    return [a for a in addresses if a not in seen]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-addresses", type=int, default=15)
    ap.add_argument("--seconds", type=float, default=90.0)
    args = ap.parse_args()

    config = load_config()
    roster = load_roster()
    if not roster:
        print("[measure] no roster yet - nothing to measure")
        return 0

    cache = ActivityCache(DATA_DIR / "labels" / "address_activity.json",
                          max_lookups=args.max_addresses * len(CHAINS),
                          seconds=args.seconds)
    todo = unmeasured(candidates(roster, config), cache._table)
    print(f"[measure] {len(todo)} candidates unmeasured; budget "
          f"{args.max_addresses} addresses / {args.seconds:.0f}s")

    busy = contracts = quiet = 0
    for address in todo[:args.max_addresses]:
        readings = []
        for chain in CHAINS:
            try:
                readings.append(cache.get(address, chain))
            except Exception as exc:                            # noqa: BLE001
                print(f"[measure]   {address[:12]} {chain}: {exc}")
        if cache.out_of_time:
            print("[measure] out of time - the rest go to the next run")
            break
        if any(is_busy(r) for r in readings if r):
            busy += 1
            verdict = "BUSY"
        elif any((r or {}).get("is_contract") for r in readings):
            contracts += 1
            verdict = "CONTRACT"
        elif any(readings):
            quiet += 1
            verdict = "quiet"
        else:
            verdict = "unreadable"
        print(f"[measure]   {address[:12]} {verdict}")

    print(f"[measure] done: {busy} busy, {contracts} contracts, {quiet} quiet "
          f"({cache.lookups} lookups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
