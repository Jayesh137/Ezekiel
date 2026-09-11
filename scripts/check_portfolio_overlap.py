#!/usr/bin/env python3
"""Score roster wallets on holding the same UNUSUAL basket as the target.

One free `clearinghouseState` call per wallet. Rarity does all the work: the
target's book is currently 52 positions, and every large book overlaps every
other on BTC, ETH and HYPE, so only markets almost nobody holds contribute.

Writes evidence, raises no alert. A copy-trader holds the same basket in the
same direction at the same time by definition, and this project exists because
its owner copies this trader by hand — so a high score is exactly as consistent
with a copycat as with the man himself. It informs a human looking at a lead; it
must not promote one.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.portfolio_overlap import basket, build_report, save
from src.scanner import live_hip3_dexes, merged_clearinghouse_state
from src.utils import DATA_DIR, load_config

MAX_CANDIDATES = 40


def candidate_wallets(config: dict) -> list[str]:
    target = (config.get("target_wallet") or "").lower()
    out, seen = [], {target}
    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            rows = json.load(f).get("wallets", [])
    except (OSError, ValueError, AttributeError):
        rows = []
    for row in rows:
        a = (row.get("wallet") or "").lower()
        if not a or a in seen or row.get("tier") == "INFRASTRUCTURE":
            continue
        seen.add(a)
        out.append(a)
    return out[:MAX_CANDIDATES]


def main() -> int:
    config = load_config()
    try:
        # HIP-3 books included, and EVERY live dex for the target rather than
        # the one he is known to use: a book on another is exactly what a
        # migration inside Hyperliquid would look like.
        target_state = merged_clearinghouse_state(config["target_wallet"],
                                                  dexes=live_hip3_dexes())
    except Exception as exc:                          # noqa: BLE001 - transport
        print(f"[portfolio] target state unreadable: {type(exc).__name__}: {exc}")
        return 0

    states = {}
    for wallet in candidate_wallets(config):
        try:
            st = merged_clearinghouse_state(wallet)
        except Exception:                             # noqa: BLE001 - transport
            continue
        # Only wallets actually holding something. A closed book is not evidence
        # of anything either way.
        if basket(st):
            states[wallet] = st
        time.sleep(0.12)

    report = build_report(target_state, states)
    save(report)

    print(f"[portfolio] target holds {report['target_positions']} position(s); "
          f"{report['candidates_scored']} candidate(s) with an open book")
    overlaps = report["overlaps"]
    if not overlaps:
        print("[portfolio] no candidate shares a rare market with him")
        return 0
    for wallet, detail in list(overlaps.items())[:10]:
        rare = ", ".join(f"{s['market']}({s['rarity']:.2f})"
                         for s in detail["shared_rare"][:4])
        print(f"[portfolio]   {wallet[:14]}... {detail['score']:.4f}  {rare}")
    print("[portfolio] evidence only — a copy-trader holds the same basket by "
          "definition, so this never promotes a wallet on its own")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
