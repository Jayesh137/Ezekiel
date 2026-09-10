#!/usr/bin/env python3
"""Strip counterfeit value from stored records.

A transfer was priced from its `tokenSymbol`, which the sender chooses. Anyone
can deploy a token called "USDC" for a few cents, and address-poisoning kits do
exactly that — one observed transaction carried 599 transfers of a token
symboled USDC from an unverified contract Arbiscan flags as "poor reputation".

Every record already stored `token_address`. Nothing ever compared it.

Measured 2026-09-10 across the whole substrate, for the ONE (chain, symbol) pair
with a canonical contract verifiable from config:

    counterfeit booked as real   $4,480,222,225.35   (1,854 records)
    genuine, verified contract   $3,172,693,745.73

So more than half the dollar figures in the system were fake. They inflate the
fund accounting, every node's received-from-target total, the frontier's
value-based chase priority, and the amounts the correlator matches on.

This pass is deliberately narrow: it only touches a record whose (chain, symbol)
has a KNOWN canonical contract and whose token_address is a different one. A
wrong entry in that map would quarantine real money, which is worse than the bug
it fixes, so anything uncatalogued is left exactly as it is.

Idempotent: a record already marked stays marked, and nothing is re-counted.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.assets import is_impostor, load_canonical_contracts
from src.chain.collect import TRANSFERS_DIR
from src.utils import DATA_DIR, atomic_write_json, load_config

IMPOSTOR_BASIS = "impostor_token"
IMPOSTOR_REASON = "impostor_token"


def scrub_records(records: list, canonical: dict) -> tuple[int, float]:
    """Mark impostor records in place. Returns (marked, usd_removed)."""
    marked, removed = 0, 0.0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("value_basis") == IMPOSTOR_BASIS:
            continue                                   # already done
        if not is_impostor(rec.get("asset"), rec.get("token_address"),
                           rec.get("chain"), canonical):
            continue
        removed += float(rec.get("amount_usd") or 0)
        # None, never 0.0: a forgery has no dollar value, and 0.0 is a number
        # that thresholds silently accept.
        rec["amount_usd"] = None
        rec["value_basis"] = IMPOSTOR_BASIS
        rec["spam"] = True
        rec["spam_reason"] = IMPOSTOR_REASON
        marked += 1
    return marked, removed


def main() -> int:
    apply = "--apply" in sys.argv
    canonical = load_canonical_contracts(
        load_config(), DATA_DIR / "labels" / "token_contracts.json")
    if not canonical:
        print("[impostor] no canonical contracts known — nothing can be judged")
        return 0
    print(f"[impostor] {len(canonical)} canonical (chain, symbol) pair(s) known")

    root = Path(TRANSFERS_DIR)
    if not root.exists():
        print("[impostor] no substrate")
        return 0

    total_marked, total_removed, files = 0, 0.0, 0
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(chain_dir.glob("*.json")):
            try:
                with open(path) as f:
                    records = json.load(f)
            except (OSError, ValueError):
                print(f"[impostor] unreadable, skipped: {path}")
                continue
            if not isinstance(records, list):
                continue
            marked, removed = scrub_records(records, canonical)
            if not marked:
                continue
            total_marked += marked
            total_removed += removed
            files += 1
            if apply:
                atomic_write_json(path, records)

    verb = "quarantined" if apply else "would quarantine"
    print(f"[impostor] {verb} {total_marked} record(s) in {files} file(s), "
          f"removing ${total_removed:,.2f} of counterfeit value")
    if not apply:
        print("[impostor] dry run — pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
