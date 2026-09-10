# scripts/reprice_transfers.py
"""Fill in prices for stored records that never got one.

Pricing happens once, at collection time, against a deliberately tiny request
budget — a dozen or so per sweep against a free tier that rate-limits. A sweep
that collects thousands of records therefore prices a handful, and everything
else lands on disk as `price_unavailable`: retained, counted, and dropped by
`transfer_graph.normalise_transfer_record`, so it never becomes a graph edge.

This is the pass that goes back for them. It runs daily rather than per sweep
because that is the cadence the design assumes — the price cache fills a little
each run, and this converts that into coverage.

Because lookups are grouped by (asset, date), a budget of ~100 requests covers
up to 100 asset-days, each of which may hold many transfers. Progress per run
is a great deal larger than the request count suggests.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.prices import coingecko_price_lookup
from src.chain.reprice import reprice_stored_records
from src.utils import DATA_DIR, load_config, save_latest

# Sibling of data/transfers/, never inside it: `collect.records_for` treats
# every subdirectory of data/transfers/ as a chain and would try to read this
# as transfer records. data/transfers_spam/ is a sibling for the same reason.
REPRICE_DIR = DATA_DIR / "transfers_reprice"

# Sized for analyze.yml's 30-minute job, which also compacts data, rebuilds the
# fingerprint and builds the trader profile. At prices.THROTTLE_SECONDS (2.5s)
# the time budget binds first: 240s allows ~96 requests, so the call ceiling is
# a backstop rather than the limit. Roughly four minutes added to a job that
# has substantially more slack than the trace job's ~39s.
DEFAULT_MAX_CALLS = 100
DEFAULT_MAX_SECONDS = 240.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, default=None,
                        help="request ceiling for this run")
    parser.add_argument("--time-budget-seconds", type=float, default=None,
                        help="wall-clock ceiling for this run")
    args = parser.parse_args(argv)

    cfg = load_config().get("reprice") or {}
    max_calls = (args.max_calls if args.max_calls is not None
                 else cfg.get("max_calls_per_run", DEFAULT_MAX_CALLS))
    max_seconds = (args.time_budget_seconds if args.time_budget_seconds is not None
                   else cfg.get("time_budget_seconds", DEFAULT_MAX_SECONDS))

    price_lookup = coingecko_price_lookup(
        DATA_DIR / "prices", max_requests=max_calls, max_seconds=max_seconds)

    health = reprice_stored_records(price_lookup)

    print(f"[reprice] examined {health['examined']} unpriced record(s) in "
          f"{health['groups_tried']} (asset, date) group(s)")
    print(f"[reprice] repriced {health['repriced']}, "
          f"still unpriced {health['still_unpriced']}, "
          f"rewrote {health['files_rewritten']} file(s)")
    if health["still_unpriced"]:
        # Not a failure, but only the reachable part is a backlog. The rest
        # predates the price source's history window and will never resolve on
        # this key tier, so telling an operator to watch the combined number
        # trend down points them at a figure that cannot move.
        stuck = health.get("unpriceable_out_of_window", 0)
        pending = health["still_unpriced"] - stuck
        if pending:
            print(f"[reprice] {pending} record(s) still reachable and unpriced — "
                  f"expected while the price cache fills; watch that it trends down")
        if stuck:
            print(f"[reprice] {stuck} record(s) predate the price source's "
                  f"history window and cannot be priced without an API key — "
                  f"this number will not shrink on its own")

    save_latest(str(REPRICE_DIR), health)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
