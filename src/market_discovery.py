"""Free public trade polling, optional streaming, and offline observation import."""

import json
import time
from pathlib import Path

from src.utils import hl_read


def exclusions(config):
    # The configured excluded and service addresses too: the scanner was scoring
    # the zero address, which appears as a party to venue trades (2026-10-08).
    return [config.get("target_wallet", ""), *config.get("known_self_wallets", []),
            *config.get("hl_shared_destinations", []),
            *config.get("discovery", {}).get("excluded_wallets", []),
            *config.get("excluded_addresses", []),
            *config.get("known_service_addresses", [])]


def select_markets(config, universe, rotation=0):
    opts = config.get("discovery", {})
    cap = max(1, min(100, int(opts.get("max_markets", 12))))
    preferred = list(dict.fromkeys(opts.get("markets") or ["BTC", "ETH", "HYPE"]))
    others = sorted(set(universe) - set(preferred))
    # Preserve a slot for exploration whenever an alternative market exists.
    fixed = preferred[:max(1, cap - 1)] if others and cap > 1 else preferred[:cap]
    tail = preferred[len(fixed):] + others
    if tail and cap == 1 and rotation % 4 == 3:
        return [tail[(rotation // 4) % len(tail)]]
    offset = rotation % len(tail) if tail else 0
    return (fixed + tail[offset:] + tail[:offset])[:cap]


def _read(fetch, body):
    result = fetch(body)
    if isinstance(result, dict) and "ok" in result:
        if not result["ok"]:
            raise ValueError(result.get("error") or "read failed")
        return result.get("data")
    return result


def market_universe(config, fetch, rotation=0):
    opts = config.get("discovery", {})
    if opts.get("discover_markets", True) is False:
        return opts.get("universe", []), []
    names, errors = set(opts.get("universe", [])), []
    dexes = list(config.get("hip3_dexes") or [])
    try:
        extra = _read(fetch, {"type": "perpDexs"})
        if isinstance(extra, list):
            dexes += [r["name"] for r in extra if isinstance(r, dict) and r.get("name")]
    except Exception as exc:  # endpoint failure must not disable configured markets
        errors.append(f"perpDexs: {exc}")
    dexes = sorted(set(dexes))
    offset = rotation % len(dexes) if dexes else 0
    # Native + at most three HIP-3 metadata calls each run.
    selected = [""] + (dexes[offset:] + dexes[:offset])[:3]
    for dex in selected:
        try:
            meta = _read(fetch, {"type": "meta", **({"dex": dex} if dex else {})})
            if not isinstance(meta, dict) or not isinstance(meta.get("universe"), list):
                raise ValueError("invalid market universe")
            for row in meta["universe"]:
                if isinstance(row, dict) and isinstance(row.get("name"), str) and not row.get("isDelisted"):
                    name = row["name"]
                    names.add(f"{dex}:{name}" if dex and ":" not in name else name)
        except Exception as exc:
            errors.append(f"meta/{dex or 'native'}: {exc}")
    return sorted(names), errors


def collect_once(config, store, fetch=None, now_ms=None):
    if fetch is None:
        def fetch(body):
            # Metadata/recentTrades cost roughly 20 request-weight units;
            # keep this collector below the shared 1200/minute IP ceiling.
            time.sleep(1.1)
            return hl_read(body)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    rotation = store.meta("market_rotation", 0)
    universe, errors = market_universe(config, fetch, rotation)
    markets = select_markets(config, universe, rotation)
    store.set_exclusions(exclusions(config))
    totals = dict(inserted=0, duplicates=0, rejected=0, conflicts=0, outside_retention=0)
    successful = 0
    for coin in markets:
        try:
            rows = _read(fetch, {"type": "recentTrades", "coin": coin})
            if not isinstance(rows, list):
                raise ValueError("expected trade array")
            # A response for another market cannot establish coverage of this one.
            matched = [r for r in rows if isinstance(r, dict) and r.get("coin") == coin]
            result = store.ingest_trades(matched, now_ms)
            successful += 1
            for key, value in result.items():
                totals[key] += value
            times = [r.get("time") for r in matched if isinstance(r.get("time"), (int, float))]
            store.record_observation("poll", now_ms, market=coin, status="ok", mode="snapshot",
                                     earliest_event_ms=min(times) if times else None,
                                     latest_event_ms=max(times) if times else None,
                                     response_count=len(rows), rejected_market_rows=len(rows) - len(matched),
                                     unobserved_between_polls=True, **result)
        except Exception as exc:
            errors.append(f"{coin}: {exc}")
            store.record_observation("poll", now_ms, market=coin, status="error", error=str(exc)[:200])
    # Committed events precede scheduler progress; a crash can repeat a page safely.
    store.set_meta("market_rotation", rotation + 1)
    if successful:
        store.set_meta("last_successful_market_read_ms", now_ms)
    if totals['inserted'] or totals['duplicates']:
        store.set_meta("last_positive_market_observation_ms", now_ms)
    return {**totals, "markets": markets, "errors": errors, "coverage": store.coverage(),
            "successful_market_reads": successful,
            "status": "error" if not successful else "partial" if errors or totals["rejected"] else "ok"}


def collect_stream(config, store, *, connect=None, fetch=None, seconds=300,
                   max_messages=100_000, max_reconnects=5, sleep=time.sleep, clock=time.time):
    if connect is None:
        from websockets.sync.client import connect
    started = clock()
    deadline = started + max(1, min(seconds, 86400))
    initial = collect_once(config, store, fetch=fetch, now_ms=int(clock() * 1000))
    markets = initial["markets"]
    messages = attempts = 0
    errors = []
    while clock() < deadline and messages < max_messages and attempts <= max_reconnects:
        attempts += 1
        try:
            with connect("wss://api.hyperliquid.xyz/ws", open_timeout=10, close_timeout=5,
                         max_size=2**20, max_queue=16) as socket:
                for coin in markets:
                    socket.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": coin}}))
                store.record_observation("stream", int(clock() * 1000), reason="connected",
                                         markets=markets, status="awaiting_data")
                last_record = clock()
                while clock() < deadline and messages < max_messages:
                    try:
                        payload = json.loads(socket.recv(timeout=max(.01, min(15, deadline - clock()))))
                    except TimeoutError:
                        continue
                    if payload.get("channel") != "trades" or not isinstance(payload.get("data"), list):
                        continue
                    data = [r for r in payload["data"] if isinstance(r, dict) and r.get("coin") in markets]
                    result = store.ingest_trades(data, int(clock() * 1000))
                    messages += 1
                    if clock() - last_record >= 30 or messages == 1:
                        store.set_meta("last_successful_market_read_ms", int(clock() * 1000))
                        if result['inserted'] or result['duplicates']:
                            store.set_meta("last_positive_market_observation_ms", int(clock() * 1000))
                        store.record_observation("stream", int(clock() * 1000), reason="data_received",
                                                 markets=markets, status="ok", **result)
                        last_record = clock()
        except Exception as exc:
            errors.append(str(exc)[:200])
            store.record_observation("stream", int(clock() * 1000), reason="disconnect",
                                     status="gap", error=str(exc)[:200], markets=markets)
            if attempts > max_reconnects or clock() >= deadline:
                break
            sleep(min(30, 2 ** min(attempts, 5), max(0, deadline - clock())))
            # The overlap snapshot recovers what is still available, never all
            # executions lost during the disconnect. Original subscriptions stay fixed.
            collect_once({**config, "discovery": {**config.get("discovery", {}),
                          "markets": markets, "discover_markets": False, "max_markets": len(markets)}},
                         store, fetch=fetch, now_ms=int(clock() * 1000))
    store.record_observation("stream", int(clock() * 1000), reason="collector_stopped",
                             status="offline", markets=markets)
    return {"messages": messages, "connections": attempts, "errors": errors, "markets": markets}


def import_jsonl(path, store, *, now_ms=None, max_lines=1_000_000):
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    totals = dict(inserted=0, duplicates=0, rejected=0, conflicts=0, outside_retention=0, malformed_lines=0)
    lines = 0
    with Path(path).open(encoding="utf-8") as handle:
        for _ in range(max_lines):
            line = handle.readline(2**21 + 1)
            if not line:
                break
            lines += 1
            if len(line) > 2**21:
                totals["malformed_lines"] += 1
                # Drain this oversized record with bounded memory.
                while line and not line.endswith("\n"):
                    line = handle.readline(2**21)
                continue
            try:
                value = json.loads(line)
                rows = value if isinstance(value, list) else value.get("data", [value])
                if not isinstance(rows, list):
                    raise ValueError("not a trade array")
                for offset in range(0, len(rows), 2000):
                    for key, count in store.ingest_trades(rows[offset:offset + 2000], now_ms).items():
                        totals[key] += count
            except (ValueError, AttributeError, TypeError):
                totals["malformed_lines"] += 1
    store.record_observation(f"import:{Path(path).name}", now_ms, lines=lines,
                             line_budget_reached=lines == max_lines, **totals)
    return totals
