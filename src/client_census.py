"""Which CLIENT a wallet trades through — a census over the venue's takers.

The sharpest signature this project owns is his tooling: hyperliquid-python-sdk
`market_open`, which fires taker IOC orders at one round clip per coin ~1.7 s
apart in long runs, limit priced 5% through the book, with no client order id
(measured on 1,806 of his last 2,000 orders; the other 194 are `FrontendMarket`
web-UI clicks). Until now it was tested on ~25 candidates a run. `historicalOrders`
names the client class of ANY wallet in one read (2,000 orders, weight ~120), so
the class can be measured over the whole active taker population and its base
rate known — the number the casebook has lacked for a behaviour match.

A profile is compact: order counts and span, the shares of `Ioc` without client
id, `FrontendMarket`, client ids, resting orders, triggers and reduce-only closes,
the client-id prefix families, round sizes, and runs of IOC orders at a scripted
cadence. `his_class` is the shape of his script. Wallets in it are handed to the
execution-program detector (which reads their fills and measures the exact 5%
offset and clip table); nothing here votes.

Controls, recorded every run: his known wallets' classes — measured 2026-10-15,
both are web-UI accounts operated by hand, so a tooling census cannot find a
wallet of his run that way — and a split-half reading of the target himself.
Pure; the script owns the network.
"""

from __future__ import annotations

from collections import Counter
from statistics import median

MIN_ORDERS = 50
HIS_IOC_SHARE = 0.8
RECHECK_MS = 14 * 86_400_000
RUN_GAP_BREAK_MS = 30_000
RUN_MIN_ORDERS = 15
CADENCE_LOW_S, CADENCE_HIGH_S = 1.2, 3.0
MAX_PROFILES = 30_000
CLOID_PREFIX_CHARS = 8


def _order(row: dict) -> dict | None:
    order = row.get("order") if isinstance(row, dict) else None
    if not isinstance(order, dict):
        order = row if isinstance(row, dict) and "tif" in row else None
    return order


def _num(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v


def round_size(value) -> bool | None:
    """True when the size has at most two significant digits (1, 250, 0.1, 20)."""
    v = _num(value)
    if v is None or v <= 0:
        return None
    s = f"{v:.10g}"
    digits = s.replace(".", "").replace("-", "").lstrip("0").rstrip("0")
    return len(digits) <= 2


def ioc_actions(orders: list[dict]) -> list[dict]:
    """IOC orders collapsed to ACTIONS: orders sharing a coin, side and millisecond
    were sent together (measured 2026-10-15: every one of his 903 IOC timestamps
    carries exactly two same-coin orders), so cadence is between actions."""
    seen: set = set()
    actions: dict = {}
    for o in orders:
        if str(o.get("tif")) != "Ioc" or not _num(o.get("timestamp")):
            continue
        key = (o.get("coin"), o.get("side"), int(o["timestamp"]))
        if key in actions:
            actions[key]["orders"] += 1
            continue
        seen.add(key)
        actions[key] = {"coin": o.get("coin"), "side": o.get("side"), "timestamp": int(o["timestamp"]),
                        "orders": 1}
    return sorted(actions.values(), key=lambda a: a["timestamp"])


def ioc_runs(orders: list[dict]) -> int:
    """Runs of ≥15 same-coin same-side IOC ACTIONS at a 1.2–3 s median cadence."""
    runs, current = 0, []
    for a in ioc_actions(orders):
        if current and (a["coin"] != current[-1]["coin"] or a["side"] != current[-1]["side"]
                        or a["timestamp"] - current[-1]["timestamp"] > RUN_GAP_BREAK_MS):
            runs += _is_run(current)
            current = []
        current.append(a)
    runs += _is_run(current)
    return runs


def _is_run(run: list[dict]) -> int:
    if len(run) < RUN_MIN_ORDERS:
        return 0
    gaps = [(b["timestamp"] - a["timestamp"]) / 1000 for a, b in zip(run, run[1:], strict=False)]
    return int(CADENCE_LOW_S <= median(gaps) <= CADENCE_HIGH_S)


def profile(rows: list[dict]) -> dict:
    """A wallet's client profile from `historicalOrders` rows. Pure."""
    orders, oids = [], set()
    for o in (_order(r) for r in rows or []):
        if not o:
            continue
        oid = o.get("oid")
        if oid is not None:
            if oid in oids:
                continue                 # one order, listed twice
            oids.add(oid)
        orders.append(o)
    n = len(orders)
    out = {"orders": n, "span_days": None, "first_ms": None, "last_ms": None, "coins": 0,
           "ioc_nocloid_share": None, "frontend_share": None, "cloid_share": None,
           "resting_share": None, "trigger_share": None, "reduce_only_share": None,
           "round_size_share": None, "cloid_prefixes": {}, "ioc_runs": 0,
           "orders_per_action": None, "measured": False}
    if n == 0:
        return out
    ts = [t for t in (_num(o.get("timestamp")) for o in orders) if t]
    if ts:
        out["first_ms"], out["last_ms"] = int(min(ts)), int(max(ts))
        out["span_days"] = round((max(ts) - min(ts)) / 86_400_000, 2)
    out["coins"] = len({o.get("coin") for o in orders if o.get("coin")})
    tif = [str(o.get("tif") or "") for o in orders]
    cloid = [bool(o.get("cloid")) for o in orders]
    out["ioc_nocloid_share"] = round(sum(1 for t, c in zip(tif, cloid, strict=False)
                                         if t == "Ioc" and not c) / n, 4)
    out["frontend_share"] = round(sum(1 for t in tif if t == "FrontendMarket") / n, 4)
    out["cloid_share"] = round(sum(cloid) / n, 4)
    out["resting_share"] = round(sum(1 for t in tif if t in ("Gtc", "Alo")) / n, 4)
    out["trigger_share"] = round(sum(1 for o in orders if o.get("isTrigger") or o.get("isPositionTpsl")) / n, 4)
    out["reduce_only_share"] = round(sum(1 for o in orders if o.get("reduceOnly")) / n, 4)
    rounds = [r for r in (round_size(o.get("origSz") or o.get("sz")) for o in orders) if r is not None]
    out["round_size_share"] = round(sum(rounds) / len(rounds), 4) if rounds else None
    prefixes = Counter(str(o["cloid"])[:CLOID_PREFIX_CHARS] for o in orders if o.get("cloid"))
    out["cloid_prefixes"] = dict(prefixes.most_common(3))
    out["ioc_runs"] = ioc_runs(orders)
    actions = ioc_actions(orders)
    if actions:
        out["orders_per_action"] = round(sum(a["orders"] for a in actions) / len(actions), 3)
    out["measured"] = n >= MIN_ORDERS
    return out


def his_class(prof: dict) -> bool:
    """The shape of his script: mostly IOC without client ids, fired in scripted runs."""
    return bool(prof.get("measured") and (prof.get("ioc_nocloid_share") or 0) >= HIS_IOC_SHARE
                and int(prof.get("ioc_runs") or 0) >= 1)


def client_label(prof: dict) -> str:
    if not prof.get("measured"):
        return "unmeasured"
    if his_class(prof):
        return "sdk_ioc_loop"
    if (prof.get("frontend_share") or 0) >= 0.8:
        return "web_ui"
    if (prof.get("cloid_share") or 0) >= 0.8:
        return "cloid_bot"
    if (prof.get("resting_share") or 0) >= 0.8:
        return "maker"
    return "mixed"


def compact(prof: dict, now_ms: int) -> dict:
    """What the store keeps per wallet."""
    return {"t": int(now_ms), "n": prof.get("orders"), "sd": prof.get("span_days"),
            "ioc": prof.get("ioc_nocloid_share"), "fe": prof.get("frontend_share"),
            "cl": prof.get("cloid_share"), "rs": prof.get("resting_share"),
            "rd": prof.get("round_size_share"), "runs": prof.get("ioc_runs"),
            "opa": prof.get("orders_per_action"),
            "cp": list((prof.get("cloid_prefixes") or {}).keys())[:2],
            "lab": client_label(prof), "his": his_class(prof)}


def due(profiles: dict, wallet: str, now_ms: int) -> bool:
    entry = (profiles or {}).get(wallet)
    return entry is None or now_ms - int(entry.get("t") or 0) >= RECHECK_MS


def pick_wallets(population: dict, sketches: dict | None, registry: list[str], profiles: dict,
                 now_ms: int, limit: int) -> list[str]:
    """Who to read next: program-shaped venue takers first, then registry wallets,
    then the rest of the population most recently seen first. Never a wallet read
    within RECHECK_MS."""
    wallets = (population or {}).get("wallets") or {}
    ordered: list[str] = []
    seen: set = set()

    def add(w):
        if w and w not in seen and due(profiles, w, now_ms):
            seen.add(w)
            ordered.append(w)

    for w, e in sorted(wallets.items(), key=lambda kv: -int(kv[1].get("p") or 0)):
        if int(e.get("p") or 0) > 0:
            add(w)
    for w in registry or []:
        add(str(w).lower())
    for w, _ in sorted(wallets.items(), key=lambda kv: -int(kv[1].get("l") or 0)):
        add(w)
    for w in sorted((sketches or {}).keys()):
        add(str(w).lower())
    return ordered[:limit]


def base_rate(profiles: dict) -> dict:
    measured = [e for e in (profiles or {}).values() if e.get("lab") not in (None, "unmeasured")]
    his = [e for e in measured if e.get("his")]
    labels = Counter(e.get("lab") for e in measured)
    rate = (len(his) / len(measured)) if measured else None
    from src.execution_program import wilson_upper
    return {"read": len(profiles or {}), "measured": len(measured), "his_class": len(his),
            "rate": None if rate is None else round(rate, 4),
            "upper": wilson_upper(len(his), len(measured)) if measured else None,
            "labels": dict(labels)}


def split_half(rows: list[dict]) -> dict:
    """The target against himself: older half vs newer half of his orders."""
    orders = sorted((o for o in (_order(r) for r in rows or []) if o and _num(o.get("timestamp"))),
                    key=lambda o: o["timestamp"])
    if len(orders) < 2 * MIN_ORDERS:
        return {"measured": False}
    half = len(orders) // 2
    older, newer = profile(orders[:half]), profile(orders[half:])
    return {"measured": True, "older": compact(older, 0), "newer": compact(newer, 0),
            "agree": his_class(older) == his_class(newer)}


def cap(profiles: dict, limit: int = MAX_PROFILES) -> dict:
    if len(profiles) <= limit:
        return profiles
    keep = sorted(profiles.items(), key=lambda kv: (bool(kv[1].get("his")), int(kv[1].get("t") or 0)),
                  reverse=True)[:limit]
    return dict(keep)
