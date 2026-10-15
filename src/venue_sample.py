"""The venue's taker population from bounded samples of EVERY fill on Hyperliquid.

The per-market tape (`collect_tape.py`) reads every fill on his 43 markets. A
second account of his that trades other coins — or HIP-4 outcome contracts,
the instrument he traded full time for years and his main account has never
touched — is invisible to it by construction. Measured 2026-10-15: a venue-wide
sample (`fills: [{}]`) costs 7.7 MB for five minutes in one request: 31,482
fills, 1,261 takers, 383 coins (79 `xyz:`, 8 `para:`, 7 `io:`, 1 `mkts:`, 27 `@`
spot pairs, dozens of `#<n>` outcome contracts), and zero takers running a
program-shaped burst. So a few minutes a run is enough to keep three
populations current:

* **the active taker set**, which the client-fingerprint census reads;
* **outcome-market traders** (`#` coins), joined against his treasury sessions;
* **program-shaped takers venue-wide** — runs of equal clips at a scripted
  cadence on ANY coin at ANY size (`execution_program.program_runs`), the shape
  of his tooling without presuming his table.

Per taker the state keeps first/last seen, fills, a bounded coin list, HIP-3
dexes, outcome/spot fill counts and program runs. Bounded at `MAX_WALLETS`,
least recently seen evicted. Pure; the script owns the network.
"""

from __future__ import annotations

from collections import defaultdict

from src import execution_program as ep
from src import fills_feed as ff

MAX_WALLETS = 20_000
MAX_COINS_PER_WALLET = 24
MAX_PROGRAM_COINS = 8
OUTCOME_PREFIX = "#"
MIN_OUTCOME_FILLS = 3
MIN_TREASURY_SESSIONS = 2


def coin_class(coin: str) -> str:
    coin = str(coin or "")
    if coin.startswith(OUTCOME_PREFIX):
        return "outcome"
    if coin.startswith("@") or "/" in coin:
        return "spot"
    if ":" in coin:
        return "hip3"
    return "perp"


def empty_state() -> dict:
    return {"version": 1, "samples": 0, "fills": 0, "seconds": 0.0, "wallets": {}}


def fold_sample(state: dict, fills: list[dict], now_ms: int, *, seconds: float = 0.0) -> dict:
    """Fold one venue-wide sample into `state` (mutates). Returns what it saw."""
    orders = ff.taker_orders(fills)
    by_wallet: dict[str, list] = defaultdict(list)
    for o in orders:
        by_wallet[o["wallet"]].append(o)
    wallets = state.setdefault("wallets", {})
    outcome_takers = program_takers = 0
    coins_seen: set = set()
    for wallet, mine in by_wallet.items():
        entry = wallets.get(wallet)
        if entry is None:
            entry = wallets[wallet] = {"f": int(now_ms), "l": int(now_ms), "n": 0, "c": [], "d": {},
                                      "o": 0, "s": 0, "p": 0, "pc": []}
        entry["l"] = int(now_ms)
        entry["n"] += len(mine)
        for o in mine:
            coin = o["coin"]
            coins_seen.add(coin)
            kind = coin_class(coin)
            if kind == "outcome":
                entry["o"] += 1
            elif kind == "spot":
                entry["s"] += 1
            elif kind == "hip3":
                dex = coin.split(":", 1)[0]
                entry["d"][dex] = int(entry["d"].get(dex) or 0) + 1
            if coin not in entry["c"] and len(entry["c"]) < MAX_COINS_PER_WALLET:
                entry["c"].append(coin)
        runs = ep.program_runs(sorted(mine, key=lambda o: o["t"]))
        if runs:
            entry["p"] += len(runs)
            program_takers += 1
            for r in runs:
                if r["coin"] not in entry["pc"] and len(entry["pc"]) < MAX_PROGRAM_COINS:
                    entry["pc"].append(r["coin"])
        if any(coin_class(o["coin"]) == "outcome" for o in mine):
            outcome_takers += 1
    state["samples"] = int(state.get("samples") or 0) + 1
    state["fills"] = int(state.get("fills") or 0) + len(fills)
    state["seconds"] = float(state.get("seconds") or 0.0) + float(seconds or 0.0)
    return {"fills": len(fills), "orders": len(orders), "takers": len(by_wallet),
            "coins": len(coins_seen), "outcome_takers": outcome_takers,
            "program_takers": program_takers}


def cap(state: dict, limit: int = MAX_WALLETS) -> dict:
    wallets = state.get("wallets") or {}
    if len(wallets) <= limit:
        return state
    keep = sorted(wallets.items(), key=lambda kv: (int(kv[1].get("l") or 0), int(kv[1].get("n") or 0)),
                  reverse=True)[:limit]
    state["wallets"] = dict(keep)
    return state


def outcome_traders(state: dict, min_fills: int = MIN_OUTCOME_FILLS) -> list[dict]:
    rows = [{"wallet": w, "outcome_fills": int(e.get("o") or 0), "fills": int(e.get("n") or 0),
             "coins": [c for c in e.get("c") or [] if coin_class(c) == "outcome"][:8],
             "last_ms": e.get("l")}
            for w, e in (state.get("wallets") or {}).items() if int(e.get("o") or 0) >= min_fills]
    rows.sort(key=lambda r: (-r["outcome_fills"], r["wallet"]))
    return rows


def program_takers(state: dict) -> list[dict]:
    rows = [{"wallet": w, "program_runs": int(e.get("p") or 0), "program_coins": list(e.get("pc") or []),
             "fills": int(e.get("n") or 0), "last_ms": e.get("l")}
            for w, e in (state.get("wallets") or {}).items() if int(e.get("p") or 0) > 0]
    rows.sort(key=lambda r: (-r["program_runs"], r["wallet"]))
    return rows


def outcome_in_his_sessions(state: dict, treasury_state: dict | None, cluster: set,
                            min_sessions: int = MIN_TREASURY_SESSIONS) -> list[dict]:
    """Outcome traders also present in his treasury sessions: his instrument, his hours."""
    sessions = (treasury_state or {}).get("wallets") or {}
    cluster = {str(c).lower() for c in cluster}
    out = []
    for row in outcome_traders(state):
        if row["wallet"] in cluster:
            continue
        entry = sessions.get(row["wallet"])
        s = int((entry or {}).get("s") or 0)
        if s >= min_sessions:
            out.append({**row, "sessions_with": s, "controls_with": int((entry or {}).get("c") or 0)})
    out.sort(key=lambda r: (-r["sessions_with"], -r["outcome_fills"]))
    return out


def summary(state: dict) -> dict:
    wallets = state.get("wallets") or {}
    dexes: dict[str, int] = defaultdict(int)
    for e in wallets.values():
        for dex in (e.get("d") or {}):
            dexes[dex] += 1
    return {"samples": int(state.get("samples") or 0), "fills": int(state.get("fills") or 0),
            "seconds": round(float(state.get("seconds") or 0.0), 1), "wallets": len(wallets),
            "outcome_traders": sum(1 for e in wallets.values() if int(e.get("o") or 0) >= MIN_OUTCOME_FILLS),
            "spot_traders": sum(1 for e in wallets.values() if int(e.get("s") or 0) > 0),
            "program_takers": sum(1 for e in wallets.values() if int(e.get("p") or 0) > 0),
            "hip3_dex_takers": dict(sorted(dexes.items(), key=lambda kv: -kv[1]))}
