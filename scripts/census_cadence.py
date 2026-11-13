#!/usr/bin/env python3
"""How rare is his script's TIMING among the wallets that run his kind of script?

`execution_program.cadence` records, on every comparison, whether a candidate's
loop is paced like his (the pause between consecutive same-coin, same-side taker
orders). This measures the base rate of that match over the one population where
it could be confused with him: the client census's `sdk_ioc_loop` class (IOC, no
client id, scripted runs), which is itself under 1% of venue takers.

Measured 2026-11-13: 129 class wallets read, 106 with a readable cadence, 2 in
his band (Wilson 95% ceiling 6.6%) — both small bots, put to Hyperliquid's whole
surface and not him. Evidence, never a vote, until that ceiling is at most
`MAX_FALSE_MATCH_RATE` (rule 4).

One `userFills` read per class wallet (weight 20, ~1 a second), so it is run by
hand or on dispatch, never every run:

    python scripts/census_cadence.py [--limit N]

Writes `data/execution_program/cadence_census.json`. Strict reads: a wallet that
cannot be read is counted unreadable, never as "no cadence" (rule 5).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.check_execution_program import load_target_signature  # noqa: E402
from src import execution_program as ep  # noqa: E402
from src.cctp_feed import strict_post  # noqa: E402
from src.utils import DATA_DIR, atomic_write_json, load_config  # noqa: E402

OUT = DATA_DIR / "execution_program" / "cadence_census.json"
PROFILES = DATA_DIR / "client_census" / "profiles.json"


def class_wallets(profiles: dict, exclude: set) -> list[str]:
    """Every wallet the client census put in his class, his own wallets apart."""
    rows = profiles.get("wallets") if isinstance(profiles.get("wallets"), dict) else profiles
    return sorted(w for w, p in (rows or {}).items()
                  if isinstance(p, dict) and p.get("his") and w.lower() not in exclude)


def summarise(target_sig: dict, readings: dict, unreadable: list[str]) -> dict:
    """`readings` is wallet -> cadence (or None when too few pauses). Pure."""
    measured = {w: c for w, c in readings.items() if c}
    in_band = sorted(w for w, c in measured.items()
                     if ep.cadence_match(target_sig, {"cadence": c}))
    upper = ep.wilson_upper(len(in_band), len(measured)) if measured else None
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target_cadence": target_sig.get("cadence"),
        "population": "client census sdk_ioc_loop class",
        "read": len(readings),
        "measured": len(measured),
        "unreadable": len(unreadable),
        "in_band": len(in_band),
        "rate": round(len(in_band) / len(measured), 4) if measured else None,
        "upper": round(upper, 4) if upper is not None else None,
        "votes": bool(upper is not None and upper <= ep.MAX_FALSE_MATCH_RATE
                      and len(measured) >= ep.MIN_CENSUS_ATTEMPTS),
        "wallets_in_band": [{"wallet": w, "cadence": measured[w]} for w in in_band],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    config = load_config()
    exclude = {str(config.get("target_wallet") or "").lower(),
               *(str(w).lower() for w in config.get("known_self_wallets") or [])}
    target_sig = load_target_signature()
    if not target_sig.get("cadence"):
        print("[cadence] his own cadence is unreadable from stored fills - nothing to compare")
        return 1
    with open(PROFILES, encoding="utf-8") as handle:
        wallets = class_wallets(json.load(handle), exclude)
    if args.limit:
        wallets = wallets[:args.limit]

    readings, unreadable = {}, []
    for wallet in wallets:
        try:
            fills = strict_post({"type": "userFills", "user": wallet})
        except Exception as exc:  # strict: unreadable, never empty
            print(f"[cadence] {wallet} unreadable: {type(exc).__name__}")
            unreadable.append(wallet)
            continue
        readings[wallet] = ep.cadence(ep.reconstruct_orders(fills if isinstance(fills, list) else []))
        time.sleep(1.1)

    report = summarise(target_sig, readings, unreadable)
    atomic_write_json(OUT, report)
    print(f"[cadence] his p25 {report['target_cadence']['p25']}s p50 {report['target_cadence']['p50']}s; "
          f"{report['in_band']} of {report['measured']} measurable class wallets in his band "
          f"(ceiling {report['upper']}); votes={report['votes']}; {report['unreadable']} unreadable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
