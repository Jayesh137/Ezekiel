#!/usr/bin/env python3
"""Resolve first funders for the wallets a decision actually depends on.

Whoever paid a wallet's very first gas was there at its creation, so it is
strong ownership evidence — and it powers two of the five vectors
`classify_node` accepts as corroboration: `shared_funder` and
`gas_funded_by_target`. Both were false for every wallet in the graph, for two
compounding reasons now fixed:

  * `get_first_funder` capped its search at block 99,999,999 while Arbitrum is
    past 501,000,000, so any wallet funded after the chain's first fifth
    returned no funder at all.
  * Nothing populated a funder for graph wallets. The scanner writes linkage
    only for LEADERBOARD candidates, and 0 of 50 stored ones carried a linkage
    block.

Ordered by where an answer changes something. The roster's leads are first:
those wallets hold $623,473,212 of the target's outflow and are stuck at
POSSIBLE precisely because they have one vector and need a second.

A first funder cannot change, so a positive answer is cached permanently and
each wallet costs at most one lookup ever.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.linkage import (
    load_first_funders,
    resolve_first_funders,
    save_first_funders,
)
from src.utils import DATA_DIR, load_config

# Each miss costs up to two Etherscan calls from a free-tier budget shared with
# the sweep. The daily job can afford more than the 30-minute one.
DEFAULT_MAX_LOOKUPS = 40


def wallets_worth_resolving(config: dict) -> list[str]:
    """Wallets in the order an answer would change a conclusion.

    Leads first — they are one vector short of PROBABLE, so a funder match is
    exactly what promotes them. Then the target itself (needed before any
    `shared_funder` comparison can be made at all), then everything else.
    """
    target = (config.get("target_wallet") or "").lower()
    leads, others = [], []
    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            rows = json.load(f).get("wallets", [])
    except (OSError, ValueError, AttributeError):
        rows = []

    for row in rows:
        w = (row.get("wallet") or "").lower()
        if not w or w == target or row.get("tier") == "INFRASTRUCTURE":
            continue
        (leads if row.get("tier") in ("POSSIBLE", "WATCH") else others).append(w)

    # The target's own funder is the pivot every comparison turns on, so it is
    # resolved before the leads it would be compared against.
    return [target] + leads + others


def main() -> int:
    config = load_config()
    max_lookups = DEFAULT_MAX_LOOKUPS
    if len(sys.argv) > 1:
        try:
            max_lookups = max(0, int(sys.argv[1]))
        except ValueError:
            print(f"[funders] ignoring unparseable lookup budget {sys.argv[1]!r}")

    wallets = wallets_worth_resolving(config)
    before = load_first_funders()
    cache, spent = resolve_first_funders(wallets, max_lookups=max_lookups,
                                         cache=before)
    added = len(cache) - len(before)
    if added:
        save_first_funders(cache)

    target = (config.get("target_wallet") or "").lower()
    print(f"[funders] {len(wallets)} wallet(s) of interest, {len(before)} cached, "
          f"{spent} lookup(s) spent, {added} new")
    if cache.get(target):
        print(f"[funders] target first funded by {cache[target]}")
    else:
        print("[funders] target's own first funder still unknown — no "
              "shared-funder comparison is possible until it resolves")

    funded_by_target = [w for w, f in cache.items() if f == target and w != target]
    if funded_by_target:
        print(f"[funders] {len(funded_by_target)} wallet(s) first funded BY the "
              f"target:")
        for w in funded_by_target[:10]:
            print(f"[funders]   {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
