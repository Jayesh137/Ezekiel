#!/usr/bin/env python3
"""Sample the live trade tape for his markets and catch his program running live.

The free-setup recovery of the real-time behavioural net: a bounded websocket
window (a few minutes) on GitHub Actions, driven on the usual cadence. It streams
his markets, finds any wallet running a program-shaped burst at one of his exact
per-coin clip sizes, and registers those wallets as execution-program candidates —
which the census-gated detector then examines fully next trace run. It does not
alert on a clip burst directly (a single-coin live burst is a lead, not proof), so
it adds coverage without a noisy new alert path. Off-leaderboard, off-roster
wallets are reachable here that no per-wallet sweep would find in time.

Since 2026-10-12 every window is also folded into the tape census
(`src/tape_census.py`): a bounded per-wallet sketch of sizes per coin, read for a
second execution census (comparability among the takers of his markets), a
venue-wide screen for his clip table or its rank structure (hits become
candidates), and newcomers in size on his rare markets, whose real birth is read
from `portfolio` and judged against his silences — the dormancy-handoff shape, and
the one alert this script raises, through the dormancy detector's own alert.

`build_report`/`register_hits`/`judge_newcomers` are pure or injectable and
unit-tested; `main` is the network shell (requires the optional `websockets`
dependency, requirements-stream.txt).
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import tape
from src import tape_census as tc
from src.utils import DATA_DIR, atomic_write_json, load_config, save_latest

OUT_DIR = DATA_DIR / "tape"
SKETCH_FILE = OUT_DIR / "sketches.json"
CENSUS_FILE = OUT_DIR / "census.json"
DEFAULT_SECONDS = 150
WS_URL = "wss://api.hyperliquid.xyz/ws"
# Newcomer birth reads per window: one `portfolio` call each, largest first.
MAX_NEWCOMER_READS = 8


def target_clip_table():
    """His per-coin clip sizes, from the cached target signature or stored fills."""
    return target_signature().get("clip_table") or {}


def target_signature():
    """His full execution signature (clip table and notionals), cached or rebuilt."""
    try:
        with open(DATA_DIR / "execution_program" / "target.json") as handle:
            sig = json.load(handle)
            if isinstance(sig, dict) and sig.get("clip_table"):
                return sig
    except (OSError, ValueError):
        pass
    from src.execution_program import signature
    from src.fingerprint import load_fills
    return signature(load_fills())


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


def register_census_hits(hits, data_dir=None):
    """A sketch reproducing his table (or its rank structure) becomes a candidate.

    The detector reads its real fills next trace run and applies the census gate;
    nothing here mints a vote.
    """
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for hit in hits or []:
        try:
            wallet = valid_wallet(hit.get("wallet"))
        except ValueError:
            continue
        observe_candidate(wallet, {
            "source": "tape_census", "positive": True,
            "event_id": f"tapecensus:{wallet}",
            "detail": {k: hit.get(k) for k in (
                "clips_matched", "clips_compared", "clip_match_ratio", "clips_matched_coins",
                "notional_structure_rho", "notional_coins_compared")},
        }, data_dir=data_dir)
        registered.append(wallet)
    return registered


def load_sketches():
    """The committed sketches, or empty on the very first run.

    Only a missing file is a first run: a file that cannot be parsed stops the
    run (the failure issue carries it) rather than silently restarting a memory
    that the census and the newcomer rule both depend on (rule 5).
    """
    try:
        with open(SKETCH_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return tc.empty_sketches()
    except ValueError as exc:
        raise ValueError(f"{SKETCH_FILE}: not valid JSON, refusing to restart the sketch: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("wallets"), dict):
        raise ValueError(f"{SKETCH_FILE}: unexpected shape, refusing to restart the sketch")
    data.setdefault("prices", {})
    data.setdefault("windows", 0)
    return data


def load_dormancy_state():
    try:
        with open(DATA_DIR / "dormancy" / "latest.json", encoding="utf-8") as handle:
            state = json.load(handle).get("dormancy")
            return state if isinstance(state, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def cluster(config):
    out = {str(config.get("target_wallet") or "").lower()}
    for entry in config.get("known_self_wallets") or []:
        if isinstance(entry, dict):
            entry = entry.get("address")
        if isinstance(entry, str):
            out.add(entry.lower())
    for entry in config.get("excluded_addresses") or []:
        if isinstance(entry, str):
            out.add(entry.lower())
    return out


def judge_newcomers(rows, target_days, now_day, state, *, fetch, data_dir=None,
                    alert=None, limit=MAX_NEWCOMER_READS):
    """Read each newcomer's real birth and judge it against his silences.

    A newcomer whose first activity landed inside an unusual silence of his — a
    closed one in his history or the one open now — is registered as a
    `tape_handoff` candidate and alerted through the dormancy detector's own
    alert (same cooldown key, so the two never page twice for one wallet). One
    `portfolio` read each, largest notional first; an unreadable birth is
    recorded as unread, never as "not a handoff".
    """
    from src.candidate_registry import observe_candidate, valid_wallet
    from src.dormancy import MS_PER_DAY
    from src.hl_identity import parse_birth

    judged, handoffs, unread = [], [], []
    for row in (rows or [])[:limit]:
        try:
            wallet = valid_wallet(row.get("wallet"))
        except ValueError:
            continue
        response = fetch({"type": "portfolio", "user": wallet})
        if not response.get("ok"):
            unread.append({"wallet": wallet, "error": response.get("error")})
            continue
        birth = parse_birth(response.get("data"))
        if not birth:
            unread.append({"wallet": wallet, "error": "no observed portfolio birth"})
            continue
        birth_day = int(birth) // MS_PER_DAY
        verdict = tc.born_in_silence(target_days, birth_day, now_day, state)
        judged.append({**row, "birth_day": birth_day, **verdict})
        if verdict.get("score", 0) > 0:
            detail = {**verdict, "birth_day": birth_day, "notional_usd": row.get("notional_usd"),
                      "rare_markets": row.get("rare_markets")}
            observe_candidate(wallet, {
                "source": "tape_handoff", "positive": True,
                "event_id": f"tapehandoff:{wallet}", "detail": detail,
            }, data_dir=data_dir)
            handoffs.append({"wallet": wallet, **detail})
            if alert is not None:
                alert(wallet, detail)
    return {"judged": judged, "handoffs": handoffs, "unread": unread,
            "deferred": max(0, len(rows or []) - limit)}


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


def fold_and_census(trades, target_sig, config, *, now_ms=None, fetch=None, alert=None,
                    data_dir=None, sketches=None, state=None, target_days=None):
    """Fold the window into the sketches; census, screen and judge newcomers.

    Returns the census and what the window contributed; writes nothing itself.
    """
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    sketches = sketches if sketches is not None else tc.empty_sketches()
    exclude = cluster(config)
    target_coins = set((target_sig.get("clip_table") or {}).keys())
    orders = [o for o in tape.taker_orders(trades) if o["wallet"] not in exclude]
    window = tc.fold_window(sketches, orders, now_ms)
    counts = tc.market_counts(sketches)
    fresh = tc.newcomers(window, counts, target_coins, exclude=exclude)
    census = tc.census(sketches, target_sig, exclude=exclude)
    census["computed_at"] = datetime.now(UTC).isoformat()
    tc.cap(sketches, target_coins=target_coins)
    judged = None
    if fresh and fetch is not None:
        judged = judge_newcomers(fresh, target_days or [], now_ms // 86_400_000, state,
                                 fetch=fetch, data_dir=data_dir, alert=alert)
    return {"sketches": sketches, "census": census, "window": window, "newcomers": fresh,
            "judged": judged, "rare_markets": sorted(
                m for m in target_coins if int(counts.get(m) or 0) <= tc.RARE_MARKET_TAKERS)}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=DEFAULT_SECONDS)
    args = parser.parse_args()

    target_sig = target_signature()
    clips = target_sig.get("clip_table") or {}
    if not clips:
        print("[tape] no target clip table yet; collect fills first")
        return None
    config = load_config()
    # His markets: the coins he runs clips on, plus his configured HIP-3 dex coins.
    markets = sorted(set(clips) | {f"{dex}:{c.split(':')[-1]}" for dex in config.get("hip3_dexes", [])
                                   for c in clips if c.startswith(f"{dex}:")})
    sketches = load_sketches()   # before the stream: a damaged memory fails fast
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

    from src.alerts import alert_dormancy_handoff
    from src.dormancy import active_days
    from src.fingerprint import load_fills
    from src.utils import hl_read
    target_days = active_days(load_fills())
    folded = fold_and_census(trades, target_sig, config, fetch=hl_read,
                             alert=alert_dormancy_handoff, sketches=sketches,
                             state=load_dormancy_state(), target_days=target_days)
    census = folded["census"]
    registered = register_census_hits(census.get("hits"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(SKETCH_FILE, folded["sketches"], sort_keys=True, compact=True)
    atomic_write_json(CENSUS_FILE, census)
    judged = folded["judged"] or {}
    report["census"] = {k: census.get(k) for k in (
        "attempted", "population", "comparable_rate", "comparable_upper", "sketch_wallets",
        "windows", "ratio_p99", "rho_p99")}
    report["census_hits"] = len(census.get("hits") or [])
    report["census_registered"] = registered
    report["newcomers"] = folded["newcomers"][:20]
    report["newcomer_handoffs"] = judged.get("handoffs") or []
    report["newcomers_unread"] = judged.get("unread") or []
    report["rare_markets"] = folded["rare_markets"]
    save_latest(str(OUT_DIR), report)
    print(f"[tape] {report['trades_seen']} trades, {report['distinct_takers']} takers, "
          f"{report['hit_count']} program hits at his clips over {args.seconds}s")
    for h in report["program_hits"][:10]:
        print(f"  {h['wallet']} {h['coin']} clip={h['clip']} orders={h['orders']} gap_med={h['gap_med']}")
    print(f"[tape-census] sketches={census.get('sketch_wallets')} windows={census.get('windows')} "
          f"judgeable={census.get('attempted')} comparable={census.get('population')} "
          f"ceiling={census.get('comparable_upper')} hits={report['census_hits']} "
          f"newcomers={len(folded['newcomers'])} handoffs={len(report['newcomer_handoffs'])} "
          f"unread={len(report['newcomers_unread'])}")
    for h in report["newcomer_handoffs"]:
        print(f"  HANDOFF {h['wallet']} score={h.get('score')} born day {h.get('birth_day')} "
              f"{'(open silence)' if h.get('open') else ''} on {h.get('rare_markets')}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
