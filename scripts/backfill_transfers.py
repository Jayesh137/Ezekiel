# scripts/backfill_transfers.py
"""Re-collect the cluster's full transfer history across every enabled chain.

Run once after the substrate lands, and any time a chain is added. The regular
trace job is incremental — it resumes from a cursor — so it can never recover
history that was already evicted. On the live data, that history is 905
poisoning records occupying a 1000-row window, which pushed everything older
than 2025-11-30 out of reach and left a $13,000,000 transfer with no onward
trail.

Pass --reset for that first full re-read from block 0. Every run after that
should omit it: this job's own budget (config.json under `backfill`) is far
larger than the incremental trace job's, but still finite, and a run that gets
cut off partway needs its cursor progress intact to finish on the next
invocation — resetting unconditionally on every run would wipe that progress
and the sweep could loop back to block 0 forever without ever completing.
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
    save_sweep_health,
    sweep_wallet,
    write_cursors,
)
from src.utils import load_config


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
    parser.add_argument("--reset", action="store_true",
                        help="clear stored cursors first for a full re-read from block 0; "
                             "default is to resume from wherever the last run stopped")
    args = parser.parse_args(argv)

    config = load_config()
    wallets = [w.lower() for w in args.wallet] if args.wallet else cluster_wallets(config)
    collection = config.get("collection") or {}
    backfill_cfg = config.get("backfill") or {}

    if args.reset:
        reset_cursors(wallets)
        print(f"[backfill] cursors reset for {len(wallets)} wallet(s) — reading full history")
    else:
        print(f"[backfill] resuming from stored cursors for {len(wallets)} wallet(s)")

    # backfill's own budget first — this job gets a 60-minute timeout, not the
    # incremental trace job's 10 — falling back to `collection` and then the
    # historical literals so a config written before this key keeps working.
    budget = CallBudget(
        max_calls=backfill_cfg.get("max_calls_per_run",
                                   collection.get("max_calls_per_run", 2500)),
        seconds=backfill_cfg.get("time_budget_seconds",
                                 collection.get("time_budget_seconds", 420)),
    )

    results = []
    for wallet in wallets:
        print(f"[backfill] sweeping {wallet} across "
              f"{len(enabled_chains(config))} chain(s)")
        results.append(sweep_wallet(wallet, enabled_chains(config), budget,
                                    cluster=True))

    # Merging rather than clobbering: the trace job writes this same file every
    # 30 minutes for the target alone, and a --wallet run here sweeps something
    # else entirely. Whichever wrote last must not erase the other's record of
    # which chains it could not read.
    health = save_sweep_health(results, str(TRANSFERS_DIR))
    print(f"[backfill] {health['records']} record(s), "
          f"{health['spam_suppressed']} suppressed as spam, "
          f"{health['calls']} API call(s)")
    if health["degraded_sources"]:
        print(f"[backfill] DEGRADED: could not fully read {health['degraded_sources']}")

    # sweep_health only totals gap counts; naming which chains stopped short
    # needs the per-chain detail sweep_wallet returned, still intact in `results`.
    # A budget-truncated run must never exit looking the same as a complete one.
    truncated = sorted({
        name for res in results for name, chain_result in res["chains"].items()
        if chain_result["truncated"] or chain_result["gaps"]
    })
    if truncated:
        print(f"[backfill] TRUNCATED: budget ran out before finishing {truncated} "
              f"— re-run (without --reset) to continue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
