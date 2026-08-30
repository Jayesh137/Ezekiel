# scripts/backfill_transfers.py
"""Re-collect the cluster's full transfer history across every enabled chain.

Run once after the substrate lands, and any time a chain is added. The regular
trace job is incremental — it resumes from a cursor — so it can never recover
history that was already evicted. On the live data, that history is 905
poisoning records occupying a 1000-row window, which pushed everything older
than 2025-11-30 out of reach and left a $13,000,000 transfer with no onward
trail.

Resetting the cursors is what makes the sweep re-read from block 0.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import (
    TRANSFERS_DIR,
    read_cursors,
    sweep_health,
    sweep_wallet,
    write_cursors,
)
from src.utils import load_config, save_latest


def cluster_wallets(config: dict) -> list[str]:
    """The target plus every wallet already confirmed to be his, deduplicated."""
    wallets = [config["target_wallet"]] + list(config.get("known_self_wallets", []))
    seen, out = set(), []
    for w in wallets:
        wl = (w or "").lower()
        if wl and wl not in seen:
            seen.add(wl)
            out.append(wl)
    return out


def reset_cursors(wallets: list[str]) -> None:
    """Drop the resume points for these wallets so the sweep starts at block 0."""
    targets = {(w or "").lower() for w in wallets}
    cursors = read_cursors()
    kept = {k: v for k, v in cursors.items()
            if len(k.split(":")) < 2 or k.split(":")[1] not in targets}
    write_cursors(kept)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet", action="append", default=None,
                        help="sweep this address instead of the cluster; repeatable")
    parser.add_argument("--keep-cursors", action="store_true",
                        help="incremental sweep instead of full history")
    args = parser.parse_args(argv)

    config = load_config()
    wallets = [w.lower() for w in args.wallet] if args.wallet else cluster_wallets(config)
    collection = config.get("collection") or {}

    if not args.keep_cursors:
        reset_cursors(wallets)
        print(f"[backfill] cursors reset for {len(wallets)} wallet(s) — reading full history")

    budget = CallBudget(
        max_calls=collection.get("max_calls_per_run", 2500),
        seconds=collection.get("time_budget_seconds", 420),
    )

    results = []
    for wallet in wallets:
        print(f"[backfill] sweeping {wallet} across "
              f"{len(enabled_chains(config))} chain(s)")
        results.append(sweep_wallet(wallet, enabled_chains(config), budget,
                                    cluster=True))

    health = sweep_health(results)
    save_latest(str(TRANSFERS_DIR), health)
    print(f"[backfill] {health['records']} record(s), "
          f"{health['spam_suppressed']} suppressed as spam, "
          f"{health['calls']} API call(s)")
    if health["degraded_sources"]:
        print(f"[backfill] DEGRADED: could not fully read {health['degraded_sources']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
