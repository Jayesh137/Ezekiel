#!/usr/bin/env python3
"""Tripwire on the Ethereum addresses GCR's own words led to.

None of them touches Hyperliquid or the target today. The point is the day one
does: a confirmed GCR address appearing in the target's transfer graph, or
trading on Hyperliquid, would be the strongest evidence this project can
produce about who the tracked wallet is — and unlike every style check, it would
be flow rather than resemblance.

Cheap: a handful of Hyperliquid calls plus addresses already on disk.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import send_alert
from src.chain.collect import records_for
from src.gcr_wallets import build_report, load_addresses, save, watched
from src.utils import hl_post, load_config


def hl_state(addr: str) -> dict:
    """Ask Hyperliquid about one address.

    Every failure is recorded as a failure. An exception here must never
    serialise as "this address is clean" — that is the difference between
    "we could not tell" and "there is nothing there".
    """
    out = {"read_ok": True, "fills": 0, "ledger": 0, "outbound_ledger": 0,
           "account_value": "0"}
    try:
        st = hl_post({"type": "clearinghouseState", "user": addr}) or {}
        out["account_value"] = (st.get("marginSummary") or {}).get(
            "accountValue", "0")
        fills = hl_post({"type": "userFills", "user": addr}) or []
        ledger = hl_post({"type": "userNonFundingLedgerUpdates",
                          "user": addr, "startTime": 0}) or []
        out["fills"] = len(fills)
        out["ledger"] = len(ledger)
        # Inbound-only ledger rows are airdrop spam, not the address acting.
        # 0x398d2824... looks "active" on that basis alone and is not.
        out["outbound_ledger"] = sum(
            1 for e in ledger
            if ((e.get("delta") or {}).get("user") or "").lower() == addr.lower())
    except Exception as exc:                          # noqa: BLE001 - transport
        out["read_ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    data = load_addresses()
    if not data:
        print("[gcr-eth] no address labels on disk — nothing to watch")
        return 1

    config = load_config()
    target = (config.get("target_wallet") or "").lower()

    graph = set()
    try:
        for r in records_for(target):
            for key in ("src", "dst"):
                v = (r.get(key) or "").lower()
                if v:
                    graph.add(v)
    except Exception as exc:                          # noqa: BLE001
        print(f"[gcr-eth] graph unreadable: {type(exc).__name__}: {exc}")

    states = {}
    for addr in watched(data):
        states[addr] = hl_state(addr)
        time.sleep(0.3)

    report = build_report(graph_addresses=graph, hl_states=states, data=data)
    save(report)

    print(f"[gcr-eth] watching {report['watched']} address(es) against "
          f"{len(graph)} target counterparties")
    for hit in report["graph_hits"]:
        print(f"[gcr-eth]   GRAPH HIT  {hit['address']}  ({hit['tier']}) "
              f"{hit['role']}")
    for hit in report["hyperliquid_hits"]:
        print(f"[gcr-eth]   HL ACTIVE  {hit['address']}  ({hit['tier']}) "
              f"value={hit['account_value']} fills={hit['fills']}")
    for u in report["unreadable"]:
        print(f"[gcr-eth]   UNREADABLE {u['address']}  {u['why']}")
    print(f"[gcr-eth] {report['reading']}")

    if report["graph_hits"] or report["hyperliquid_hits"]:
        lines = [
            "An Ethereum address confirmed or strongly linked to GCR has moved "
            "into range of the tracked wallet.",
            "",
            "This is FLOW, not style. Every other GCR check compares habits; "
            "this one is a transaction that either happened or did not.",
            "",
        ]
        lines.extend(f"GRAPH: {h['address']} ({h['tier']}) — {h['role']}"
                     for h in report["graph_hits"])
        lines.extend(f"HYPERLIQUID: {h['address']} ({h['tier']}) — "
                     f"value {h['account_value']}, {h['fills']} fill(s)"
                     for h in report["hyperliquid_hits"])
        lines += [
            "",
            "Provenance is in data/labels/gcr_addresses.json. Verify by hand "
            "before acting: a transfer is not ownership.",
        ]
        send_alert("GCR Ethereum address in range of the target",
                   "\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
