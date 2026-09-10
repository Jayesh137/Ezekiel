#!/usr/bin/env python3
"""Test the tracked wallet against GCR's documented style, in both directions.

The operator puts the odds that this wallet is GCR's at 55-75%. This exists to
move that estimate, either way, on measured behaviour rather than to confirm it.
Writes evidence and raises no alert: a style resemblance is not a discovery, and
alerting on one would train the operator to ignore the channel.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import calibration
from src.chain.collect import records_for
from src.gcr_hypothesis import evaluate, save
from src.utils import hl_post, load_config


def main() -> int:
    config = load_config()
    target = (config.get("target_wallet") or "").lower()
    try:
        state = hl_post({"type": "clearinghouseState", "user": target})
    except Exception as exc:                          # noqa: BLE001 - transport
        print(f"[gcr] target state unreadable: {type(exc).__name__}: {exc}")
        return 0

    amounts = [float(r["amount_usd"]) for r in records_for(target)
               if (r.get("src") or "").lower() == target and r.get("amount_usd")]
    report = evaluate(state, freq=calibration.load_market_frequencies(),
                      amounts=amounts)
    save(report)

    print(f"[gcr] {report['open_positions']} open position(s); tally {report['tally']}")
    for name, r in report["results"].items():
        print(f"[gcr]   {name:<24} {r['verdict'].upper():<11} {r['detail'][:96]}")
    print(f"[gcr] {report['reading']}")
    print("[gcr] evidence only — style traits from 2021-2023 writing against "
          "2026 behaviour; they nudge the 55-75% prior, never replace it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
