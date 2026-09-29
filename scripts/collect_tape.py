#!/usr/bin/env python3
"""Sample the live trade tape for his markets and catch his program running live.

The free-setup recovery of the real-time behavioural net: a bounded websocket
window (a few minutes) on GitHub Actions, driven on the usual cadence. It streams
his markets, finds any wallet running a program-shaped burst at one of his exact
per-coin clip sizes, and registers those wallets as execution-program candidates —
which the census-gated detector then examines fully next trace run. It does not
alert directly (a single-coin live burst is a lead, not proof), so it adds coverage
without a noisy new alert path. Off-leaderboard, off-roster wallets are reachable
here that no per-wallet sweep would find in time.

`build_report`/`register_hits` are pure and unit-tested; `main` is the network shell
(requires the optional `websockets` dependency, requirements-stream.txt).
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import tape
from src.utils import DATA_DIR, load_config, save_latest

OUT_DIR = DATA_DIR / "tape"
DEFAULT_SECONDS = 150
WS_URL = "wss://api.hyperliquid.xyz/ws"


def target_clip_table():
    """His per-coin clip sizes, from the cached target signature or stored fills."""
    try:
        with open(DATA_DIR / "execution_program" / "target.json") as handle:
            table = json.load(handle).get("clip_table")
            if table:
                return table
    except (OSError, ValueError):
        pass
    from src.execution_program import signature
    from src.fingerprint import load_fills
    return signature(load_fills()).get("clip_table") or {}


def build_report(trades, target_clips, *, seconds=None, markets=None):
    orders = tape.taker_orders(trades)
    hits = tape.program_clip_hits(orders, target_clips)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "window_seconds": seconds,
        "markets_watched": sorted(markets or []),
        "trades_seen": len(trades),
        "taker_orders": len(orders),
        "distinct_takers": len({o["wallet"] for o in orders}),
        "program_hits": hits[:50],
        "hit_count": len(hits),
    }


def register_hits(hits, data_dir=None):
    """Each wallet running his clip program live becomes an execution candidate."""
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for hit in hits or []:
        try:
            wallet = valid_wallet(hit.get("wallet"))
        except ValueError:
            continue
        observe_candidate(wallet, {
            "source": "execution_program_tape", "positive": True,
            "event_id": f"tape:{wallet}:{hit.get('coin')}",
            "detail": {k: hit.get(k) for k in ("coin", "clip", "orders", "gap_med")},
        }, data_dir=data_dir)
        registered.append(wallet)
    return registered


def stream_trades(markets, seconds, *, connect=None):
    from websockets.sync.client import connect as ws_connect
    connect = connect or ws_connect
    trades, deadline = [], time.time() + seconds
    with connect(WS_URL, max_size=2**24, open_timeout=15, close_timeout=5) as socket:
        for coin in markets:
            socket.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
        while time.time() < deadline:
            try:
                message = json.loads(socket.recv(timeout=max(0.1, min(10, deadline - time.time()))))
            except TimeoutError:
                continue
            if message.get("channel") == "trades" and isinstance(message.get("data"), list):
                trades.extend(t for t in message["data"] if isinstance(t, dict))
    return trades


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=DEFAULT_SECONDS)
    args = parser.parse_args()

    clips = target_clip_table()
    if not clips:
        print("[tape] no target clip table yet; collect fills first")
        return None
    config = load_config()
    # His markets: the coins he runs clips on, plus his configured HIP-3 dex coins.
    markets = sorted(set(clips) | {f"{dex}:{c.split(':')[-1]}" for dex in config.get("hip3_dexes", [])
                                   for c in clips if c.startswith(f"{dex}:")})
    try:
        trades = stream_trades(markets, args.seconds)
    except Exception as exc:  # noqa: BLE001 - the optional dep or the socket may be unavailable
        print(f"[tape] stream unavailable: {type(exc).__name__}: {exc}")
        report = build_report([], clips, seconds=args.seconds, markets=markets)
        report["stream_error"] = str(exc)[:200]
        save_latest(str(OUT_DIR), report)
        return report
    report = build_report(trades, clips, seconds=args.seconds, markets=markets)
    register_hits(report["program_hits"])
    save_latest(str(OUT_DIR), report)
    print(f"[tape] {report['trades_seen']} trades, {report['distinct_takers']} takers, "
          f"{report['hit_count']} program hits at his clips over {args.seconds}s")
    for h in report["program_hits"][:10]:
        print(f"  {h['wallet']} {h['coin']} clip={h['clip']} orders={h['orders']} gap_med={h['gap_med']}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
