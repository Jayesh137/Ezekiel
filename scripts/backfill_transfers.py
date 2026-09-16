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

from src.chain.assets import load_par_contracts
from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import (
    TRANSFERS_DIR,
    read_cursors,
    read_records,
    save_sweep_health,
    substrate_files,
    sweep_wallet,
    write_cursors,
)
from src.chain.prices import coingecko_price_lookup
from src.chain.spam import ground_truth_addresses
from src.utils import DATA_DIR, load_config

# This job's own CoinGecko allowance, deliberately larger than
# src/tracer.py's default (12 requests / 12s): backfill.yml has ~900s of slack
# beyond config.backfill.time_budget_seconds (its own job-level comment says
# so), against the trace job's ~39s, and this script's whole purpose is deep
# historical catch-up -- exactly what a bigger allowance buys. Worst case
# added time is max_seconds + one request's own timeout (5s, prices.py's
# default) = 95s, leaving ~805s for checkout/pip/`python src/backfill.py`/push.
# See docs/superpowers/price-source-report.md for the full arithmetic.
BACKFILL_PRICE_MAX_REQUESTS = 40
BACKFILL_PRICE_MAX_SECONDS = 90.0


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



def swept_wallets_by_age(cluster: list[str]) -> list[str]:
    """Every wallet with a stored cursor, cluster first, then least-recent.

    229 wallets carry cursors on 2026-09-16. The cluster leads because a
    bounded batch must never leave the target at the back of a queue, and the
    rest follow in cursor order so repeated runs march through the list instead
    of re-reading the same head every time — the `expanded_ledger` lesson, in
    the one other place that walks a wallet list under a cap.
    """
    cursors = read_cursors()
    seen: dict[str, int] = {}
    for key, value in cursors.items():
        parts = key.split(":")
        if len(parts) < 2:
            continue
        addr = (parts[1] or "").lower()
        try:
            block = int(value or 0)
        except (TypeError, ValueError):
            block = 0
        seen[addr] = max(seen.get(addr, 0), block)
    lead = [w for w in cluster if w]
    rest = sorted((w for w in seen if w not in set(lead)), key=lambda w: seen[w])
    return lead + rest

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
    parser.add_argument("--swept", action="store_true",
                        help="sweep every wallet that already has a cursor, not just "
                             "the cluster. Use with --batch to bound one run: a "
                             "frontier-wide --reset re-reads ~640 MB into one day, "
                             "over the 100 MiB blob limit, and loses its own work")
    parser.add_argument("--batch", type=int, default=None,
                        help="sweep at most this many wallets, least recently swept "
                             "first, so repeated runs march through the list")
    parser.add_argument("--reset", action="store_true",
                        help="clear stored cursors first for a full re-read from block 0; "
                             "default is to resume from wherever the last run stopped")
    parser.add_argument("--time-budget-seconds", type=int, default=None,
                        help="override the config-derived wall-clock budget. The default "
                             "(config.backfill, 2700s) is sized for backfill.yml's 3600s "
                             "job; a caller sharing a tighter job (e.g. trace.yml's "
                             "investigate step, inside a 600s job with other steps already "
                             "budgeted) must state what it can actually afford instead")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="override the config-derived call-count budget; same "
                             "reasoning as --time-budget-seconds")
    args = parser.parse_args(argv)

    config = load_config()
    if args.wallet:
        wallets = [w.lower() for w in args.wallet]
    elif args.swept:
        wallets = swept_wallets_by_age(cluster_wallets(config))
    else:
        wallets = cluster_wallets(config)
    if args.batch:
        # Bounded on purpose. One run must leave today's chain files under the
        # 100 MiB blob limit, and the cluster is always swept first so the
        # wallets that matter are never at the back of a queue.
        wallets = wallets[:args.batch]
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
    # --time-budget-seconds/--max-calls override both fallbacks: a caller
    # invoked from inside a smaller job (trace.yml's investigate step) cannot
    # use backfill's 2700s default without guaranteeing that job is CANCELLED
    # before "Commit and push" runs — see trace.yml's job-level comment.
    budget = CallBudget(
        max_calls=(args.max_calls if args.max_calls is not None else
                  backfill_cfg.get("max_calls_per_run",
                                   collection.get("max_calls_per_run", 2500))),
        seconds=(args.time_budget_seconds if args.time_budget_seconds is not None else
                backfill_cfg.get("time_budget_seconds",
                                 collection.get("time_budget_seconds", 420))),
    )

    # One price_lookup shared across every wallet this run sweeps, so the
    # request/time ceiling is a genuine per-RUN budget rather than being
    # multiplied by however many cluster wallets there are.
    price_lookup = coingecko_price_lookup(
        Path(DATA_DIR) / "prices",
        max_requests=BACKFILL_PRICE_MAX_REQUESTS,
        max_seconds=BACKFILL_PRICE_MAX_SECONDS)

    # Canonical token contracts, so a token merely CALLED "USDC" is not priced
    # as USDC. See src/chain/assets.is_impostor.
    from src.chain.assets import load_canonical_contracts
    protected = ground_truth_addresses(config)
    par_contracts = load_par_contracts(
        DATA_DIR / "labels" / "token_contracts.json")
    canonical = load_canonical_contracts(
        config, Path(DATA_DIR) / "labels" / "token_contracts.json")

    # Only a --reset run needs this. It re-reads from block 0, so every record
    # already on disk under an earlier date comes back and append_records --
    # which dedupes against today's file alone -- would store it again. At
    # frontier scale that is ~640 MB in one day across three chain files, past
    # GitHub's 100 MiB blob limit, and check_repo_size.py fails the step before
    # the commit: the re-sweep would destroy its own output. Built ONCE and
    # shared across the wallets of the run, never per wallet, which is the
    # O(wallets x substrate) shape that already failed the linkage phase.
    known_ids = None
    if args.reset:
        known_ids = set()
        for chain_dir in sorted(p for p in Path(TRANSFERS_DIR).iterdir()
                                if p.is_dir()) if Path(TRANSFERS_DIR).exists() else []:
            for path in substrate_files(chain_dir):
                for rec in read_records(path):
                    rid = rec.get("id") if isinstance(rec, dict) else None
                    if rid:
                        known_ids.add(rid)
        print(f"[backfill] already hold {len(known_ids):,} record(s) — a "
              f"re-read will store only what is genuinely new")

    results = []
    plan_refused: dict = {}
    for wallet in wallets:
        print(f"[backfill] sweeping {wallet} across "
              f"{len(enabled_chains(config))} chain(s)")
        results.append(sweep_wallet(wallet, enabled_chains(config), budget,
                                    canonical=canonical, cluster=True,
                                    price_lookup=price_lookup,
                                    plan_refused=plan_refused,
                                    protected=protected,
                                    par_contracts=par_contracts,
                                    known_ids=known_ids))

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
