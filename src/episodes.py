"""Observed position episodes and execution habits, with censored boundaries."""

import hashlib
import json
import math
from collections import Counter
from statistics import median

SESSION_GAP_MS = 2 * 3600_000


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def fill_identity(row):
    ts = number(row.get('time'))
    stamp = int(ts) if ts is not None and ts.is_integer() else ts
    wallet = str(row.get('wallet') or '').lower()
    if row.get("tid") is not None:
        return f"fill:{wallet}:{row.get('coin')}:{stamp}:{row['tid']}"
    return "fill:" + hashlib.sha256(json.dumps({**row, 'time': stamp, 'wallet': wallet}, sort_keys=True).encode()).hexdigest()[:24]


def build_episodes(fills: list[dict], orders: list[dict] | None = None) -> list[dict]:
    valid = {}
    for row in fills:
        if not isinstance(row, dict) or row.get("side") not in ("A", "B") or not row.get("coin"):
            continue
        ts, size, price = number(row.get("time")), number(row.get("sz")), number(row.get("px"))
        if ts is None or size is None or price is None or size <= 0 or price <= 0:
            continue
        valid[fill_identity(row)] = {**row, 'time': ts}
    ordered = sorted(valid.items(), key=lambda item: (item[1]["time"], item[0]))
    if not ordered:
        return []
    # Order lifecycle metadata is bounded to the observed fill horizon. Later
    # cancellations or fills cannot leak into an earlier profile.
    from src.fingerprint import normalise_orders
    order_index = {}
    for event in normalise_orders(orders or [], end_ms=max(r["time"] for _, r in ordered)):
        order = event.get("order", event)
        if order.get("oid") is not None:
            order_index[str(order["oid"])] = order
    active, episodes = {}, []
    session, prior_ts = ordered[0][1]["time"], None

    def finish(episode):
        episode["order_count"] = len(episode["orders"])
        episode["fill_count"] = len(episode["parent_event_ids"])
        episode["orders"] = list(episode["orders"].values())
        gaps = episode.pop('gaps')
        episode["pacing_seconds"] = median(gaps) if gaps else None
        episode['entry_steps'] = sum(o['adds_exposure'] for o in episode['orders'])
        episode['exit_steps'] = sum(o['reduces_exposure'] for o in episode['orders'])
        episode["session_ids"] = sorted(episode["session_ids"])
        episodes.append(episode)

    for identity, row in ordered:
        ts, size, price = float(row["time"]), float(row["sz"]), float(row["px"])
        if prior_ts is not None and ts - prior_ts > SESSION_GAP_MS:
            session = ts
        prior_ts = ts
        key = (row.get("wallet", ""), row["coin"])
        before = number(row.get("startPosition"))
        after = before + (size if row["side"] == "B" else -size) if before is not None else None
        previous = active.get(key)
        discontinuity = bool(previous and before is not None and previous["position_after"] is not None
                             and not math.isclose(before, previous["position_after"], abs_tol=1e-8, rel_tol=1e-8))
        unknown_break = bool(previous and before is None and ts - previous["end_ms"] > 30 * 60_000)
        if previous and (discontinuity or unknown_break):
            finish(active.pop(key))
        if key not in active:
            active[key] = {"id": f"episode:{identity}", "wallet": key[0], "coin": key[1],
                           "start_ms": ts, "end_ms": ts, "position_before": before,
                           "position_after": after, "censored_start": before is None or abs(before) > 1e-8,
                           "censored_end": True, "coverage_gap": discontinuity,
                           "orders": {}, "parent_event_ids": [], "gaps": [],
                           "session_ids": set(), "notional": 0.0, "entry_steps": 0, "exit_steps": 0,
                           "pause_restarts": 0, "equity_at_start": number(row.get("account_value_usd")),
                           "position_reversal": False}
        episode = active[key]
        if episode["parent_event_ids"]:
            gap = (ts - episode["end_ms"]) / 1000
            episode["gaps"].append(gap)
            if gap > 300:
                episode["pause_restarts"] += 1
        episode["session_ids"].add(f"session:{row.get('wallet', '')}:{int(session)}")
        episode["parent_event_ids"].append(identity)
        episode["notional"] += size * price
        episode["end_ms"], episode["position_after"] = ts, after
        oid = str(row.get("oid")) if row.get("oid") is not None else identity
        order = episode["orders"].setdefault(oid, {"oid": row.get("oid"), "notional": 0.0,
                "quantity": 0.0, "fills": 0, "time_in_force": order_index.get(oid, {}).get("timeInForce"),
                "side": row["side"], "maker_fills": 0, "execution_known": 0,
                'adds_exposure': False, 'reduces_exposure': False})
        order["notional"] += size * price
        order["quantity"] += size
        order["fills"] += 1
        if isinstance(row.get("crossed"), bool):
            order["execution_known"] += 1
            order["maker_fills"] += not row["crossed"]
        if before is not None and after is not None:
            order['adds_exposure'] |= abs(after) > abs(before)
            order['reduces_exposure'] |= abs(after) < abs(before)
            reversed_position = before * after < 0
            episode["position_reversal"] |= reversed_position
            if abs(after) < 1e-8 or reversed_position:
                # A reversal closes an old position and opens another in the
                # same fill. Keep that shared observation, mark the end censored
                # instead of pretending two independent executions occurred.
                episode["censored_end"] = reversed_position
                finish(active.pop(key))
    for episode in active.values():
        finish(episode)
    return sorted(episodes, key=lambda e: (e["start_ms"], e["id"]))


def episode_profile(episodes: list[dict]) -> dict:
    orders = [o for e in episodes for o in e["orders"]]
    sizes = [o["notional"] for o in orders]
    typical = median(sizes) if sizes else None
    pacing = [e["pacing_seconds"] for e in episodes if e["pacing_seconds"] is not None]
    capital = [e["notional"] / e["equity_at_start"] for e in episodes if (e.get("equity_at_start") or 0) > 0]
    known = sum(o["execution_known"] for o in orders)
    duration = [(e["end_ms"] - e["start_ms"]) / 60_000 for e in episodes
                if not e["censored_start"] and not e["censored_end"]]
    sessions = set(s for e in episodes for s in e["session_ids"])
    transitions = Counter(f"{a['coin']}->{b['coin']}" for a, b in zip(episodes, episodes[1:], strict=False)
                          if set(a["session_ids"]) & set(b["session_ids"]) and a["coin"] != b["coin"])
    return {"episode_count": len(episodes), "session_count": len(sessions), "order_count": len(orders),
            "capital_normalised_size": median(capital) if capital else None,
            'capital_measure': 'gross_episode_turnover / observed_start_equity; not leverage or risk',
            "relative_size_dispersion": median(abs(s / typical - 1) for s in sizes) if typical else None,
            "partial_fills_per_order": median(o["fills"] for o in orders) if orders else None,
            "pacing_seconds": median(pacing) if pacing else None,
            "complete_duration_minutes": median(duration) if duration else None,
            "round_100_fraction": sum(abs(s / 100 - round(s / 100)) < 1e-6 for s in sizes) / len(sizes) if sizes else None,
            "maker_share": sum(o["maker_fills"] for o in orders) / known if known else None,
            "pause_restarts": sum(e["pause_restarts"] for e in episodes),
            "entry_steps": sum(e["entry_steps"] for e in episodes),
            "exit_steps": sum(e["exit_steps"] for e in episodes),
            "basket_transitions": dict(transitions), "markets": sorted({e["coin"] for e in episodes}),
            "parent_event_ids": sorted({p for e in episodes for p in e["parent_event_ids"]}),
            "coverage": {"censored_episodes": sum(e["censored_start"] or e["censored_end"] for e in episodes),
                         "position_gaps": sum(e["coverage_gap"] for e in episodes),
                         "equity_observed_episodes": len(capital)}}


def compare_episode_profiles(target: dict, candidate: dict) -> dict:
    features = {}
    for name in ("capital_normalised_size", "relative_size_dispersion", "partial_fills_per_order",
                 "pacing_seconds", "complete_duration_minutes", "round_100_fraction", "maker_share"):
        left, right = target.get(name), candidate.get(name)
        if left is None or right is None:
            continue
        features[name] = round(1 - abs(left - right) / max(abs(left), abs(right), 1e-8), 4)
    enough = min(target.get("session_count", 0), candidate.get("session_count", 0)) >= 5 and len(features) >= 3
    return {"status": "research" if enough else "insufficient_data", "promotable": False,
            "similarity": round(sum(features.values()) / len(features), 4) if enough else None,
            "features": features, "independent_sessions": min(target.get("session_count", 0), candidate.get("session_count", 0)),
            "confounders": ["shared_execution_software", "market_conditions"],
            "parent_event_ids": candidate.get("parent_event_ids", [])}


def regime_profiles(episodes: list[dict]) -> list[dict]:
    groups = {}
    for episode in episodes:
        groups.setdefault(int(episode["start_ms"] // (7 * 86400_000)), []).append(episode)
    return [{"start_ms": week * 7 * 86400_000, "profile": episode_profile(rows)}
            for week, rows in sorted(groups.items())]


def compact_profile(profile, limit=256):
    parents = profile.get('parent_event_ids', [])
    return {**profile, 'parent_event_ids': parents[:limit], 'parent_event_count': len(parents)}
