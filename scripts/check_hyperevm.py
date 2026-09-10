#!/usr/bin/env python3
"""Watch HyperEVM for the moment the cluster starts using it.

The target has sent $23,000,000 to the HyperCore system address for USDC across
five transfers (2026-06-12 to 2026-08-28) while his own HyperEVM nonce stayed 0.
That combination is the whole reason this runs: money is going somewhere we
cannot reconstruct in arrears, because the public RPC caps log queries at 1000
blocks against roughly one-second blocks. The nonce leaving zero is the event
worth catching, and it costs one request per wallet to watch.

Cheap by construction: three calls per wallet, no Etherscan budget, no key.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_hyperevm_activation
from src.chain.hyperevm import account_activity, describe_log_limits, summarise
from src.utils import DATA_DIR, load_config, save_latest

HYPEREVM_DIR = DATA_DIR / "hyperevm"


def _previous() -> dict:
    path = HYPEREVM_DIR / "latest.json"
    if not path.exists():
        return {}
    try:
        import json
        with open(path) as f:
            return {w["address"]: w for w in json.load(f).get("wallets", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def watched_wallets(config: dict) -> list[tuple[str, str]]:
    """(address, label) pairs. The target and anything we already believe is his.

    Deliberately small. This is a tripwire on the cluster, not a survey of every
    wallet the graph has ever touched.
    """
    out = [(config["target_wallet"].lower(), "The target wallet")]
    seen = {out[0][0]}
    for addr in config.get("known_self_wallets", []) or []:
        a = (addr or "").lower()
        if a and a not in seen:
            seen.add(a)
            out.append((a, "A known wallet of the target"))
    return out


def main() -> int:
    config = load_config()
    previous = _previous()
    wallets = watched_wallets(config)
    print(f"[hyperevm] {describe_log_limits()}")

    records, alerted, unreadable = [], 0, 0
    for address, label in wallets:
        activity = account_activity(address)
        activity["label"] = label
        records.append(activity)
        print(f"[hyperevm] {address[:12]}... {summarise(activity)}")

        if activity["nonce"] is None:
            # Never an all-clear, and never an alert either: we learned nothing.
            unreadable += 1
            for err in activity["errors"]:
                print(f"[hyperevm]   unreadable: {err}")
            continue

        before = previous.get(address, {}).get("nonce")
        # Alert only on a CONFIRMED transition from "never acted" to "acted".
        # Firing on nonce > 0 alone would alert every run forever once a wallet
        # is active, and firing when `before` is None would alert on the very
        # first run for a wallet that has been busy for a year.
        if before == 0 and activity["nonce"] > 0:
            alerted += 1
            alert_hyperevm_activation(address, label, activity["nonce"], before)
            print(f"[hyperevm]   ALERT: first HyperEVM activity "
                  f"({before} -> {activity['nonce']})")

    save_latest(str(HYPEREVM_DIR), {
        "computed_at": datetime.now(UTC).isoformat(),
        "rpc_limits": describe_log_limits(),
        "wallets": records,
        "watched": len(wallets),
        "unreadable": unreadable,
        "activations_alerted": alerted,
    })
    if unreadable:
        print(f"[hyperevm] {unreadable} wallet(s) unreadable this run — "
              f"their state is unknown, not clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
