#!/usr/bin/env python3
"""Measure Hyperliquid's explorer websocket (`explorerTxs`) for a bounded window.

A measurement tool, not a detector: it answers "what does the venue-wide L1
transaction stream carry, at what rate, and is it complete?" so that the next
session does not have to rebuild the idea to find out. Keyless; needs the
optional `websockets` dependency (requirements-stream.txt). Never run in CI.

Measured 2026-10-13 (two 40-60 s windows, docs/incident-log.md):
  - ~15 tx/s, ~7 KB/s, ~200 distinct users a minute: cheap enough for an
    always-on host to hold open for ever.
  - It is NOT the venue's order flow. 82 BTC orders from 31 users against 138
    BTC trades from 99 distinct users in the same 40 s; 97% of the orders it does
    carry are post-only (`Alo`). So it cannot serve as a venue-wide screen for
    his execution program, which is taker IOC.
  - Non-trading actions (sendToEvmWithData, usdSend, approveAgent, ...) appear
    with the signing user; whether they are complete is unmeasured.

Usage: python scripts/probe_explorer_stream.py [seconds] [--compare-coin BTC]
"""

import argparse
import asyncio
import collections
import json
import time

EXPLORER_WS = "wss://rpc.hyperliquid.xyz/ws"
API_WS = "wss://api.hyperliquid.xyz/ws"

# Actions that are not order placement or cancellation: the ones that could
# link two accounts or move money, which is what this project cares about.
LINKING_ACTIONS = {
    "approveAgent", "approveBuilderFee", "usdSend", "spotSend", "withdraw3",
    "sendToEvmWithData", "createSubAccount", "subAccountTransfer",
    "subAccountSpotTransfer", "vaultTransfer", "setReferrer", "registerReferrer",
    "convertToMultiSigUser", "multiSig", "tokenDelegate", "cDeposit", "cWithdraw",
    "usdClassTransfer", "sendAsset",
}


def summarise(txs: list[dict]) -> dict:
    """Pure: counts by action type, orders by tif, users, and linking actions."""
    types = collections.Counter()
    tif = collections.Counter()
    users = set()
    orders = 0
    linking = []
    for tx in txs:
        action = tx.get("action") if isinstance(tx, dict) else None
        if not isinstance(action, dict):
            continue
        kind = action.get("type")
        types[kind] += 1
        users.add(tx.get("user"))
        if kind == "order":
            for order in action.get("orders") or []:
                orders += 1
                limit = ((order.get("t") or {}).get("limit") or {})
                tif[limit.get("tif") or "trigger"] += 1
        elif kind in LINKING_ACTIONS:
            linking.append({"user": tx.get("user"), "type": kind,
                            "time": tx.get("time"), "hash": tx.get("hash")})
    return {"txs": len(txs), "users": len(users), "orders": orders,
            "tif": dict(tif), "types": dict(types.most_common()),
            "linking": linking}


async def _explorer(seconds: float, out: list) -> None:
    import websockets
    async with websockets.connect(EXPLORER_WS, max_size=None) as ws:
        await ws.send(json.dumps({"method": "subscribe",
                                  "subscription": {"type": "explorerTxs"}}))
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except TimeoutError:
                continue
            msg = json.loads(raw)
            data = msg.get("data") if isinstance(msg, dict) else msg
            if isinstance(data, list):
                out.extend(t for t in data if isinstance(t, dict))


async def _trades(coin: str, seconds: float, out: list) -> None:
    import websockets
    async with websockets.connect(API_WS, max_size=None) as ws:
        await ws.send(json.dumps({"method": "subscribe",
                                  "subscription": {"type": "trades", "coin": coin}}))
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except TimeoutError:
                continue
            msg = json.loads(raw)
            if isinstance(msg, dict) and msg.get("channel") == "trades":
                out.extend(msg.get("data") or [])


async def run(seconds: float, compare_coin: str | None) -> dict:
    txs: list = []
    trades: list = []
    tasks = [_explorer(seconds, txs)]
    if compare_coin:
        tasks.append(_trades(compare_coin, seconds, trades))
    await asyncio.gather(*tasks)
    report = summarise(txs)
    report["seconds"] = seconds
    report["tx_per_second"] = round(len(txs) / seconds, 1) if seconds else None
    if compare_coin:
        takers = {u for t in trades for u in (t.get("users") or [])}
        report["compare"] = {"coin": compare_coin, "trades": len(trades),
                             "trade_users": len(takers)}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("seconds", nargs="?", type=float, default=45)
    parser.add_argument("--compare-coin", default=None,
                        help="also count trades on this coin to test completeness")
    args = parser.parse_args(argv)
    report = asyncio.run(run(args.seconds, args.compare_coin))
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
