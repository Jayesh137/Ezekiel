"""Bounded recurring observation, separate from historical graph completion."""

import hashlib

COVERAGE_SCHEMA = "2026-09-26.1"
INTERVAL = 3 * 86400


def schedule_refreshes(wallets, edges, target, previous, now_ts, limit):
    state = {w.lower(): dict(row) for w, row in previous.items() if isinstance(row, dict)}
    depths = {target.lower(): 0}
    incoming, outgoing, newest = {}, {}, {}
    adjacency = {}
    for e in edges:
        src, dst = (e.get("src") or "").lower(), (e.get("dst") or "").lower()
        adjacency.setdefault(src, set()).add(dst)
        value = float(e.get("amount_usd") or 0)
        incoming[dst] = incoming.get(dst, 0) + value
        outgoing[src] = outgoing.get(src, 0) + value
        newest[dst] = max(newest.get(dst, 0), e.get("ts") or 0)
    level = [target.lower()]
    while level:
        following = []
        for src in level:
            for dst in adjacency.get(src, []):
                if dst not in depths:
                    depths[dst] = depths[src] + 1
                    following.append(dst)
        level = following
    due = []
    for wallet in dict.fromkeys(w.lower() for w in wallets):
        if wallet not in state:
            jitter = int(hashlib.sha256(wallet.encode()).hexdigest()[:12], 16) % INTERVAL
            state[wallet] = {"next_check": now_ts + 1 + jitter, "coverage_schema": COVERAGE_SCHEMA,
                             "depth": max(1, depths.get(wallet, 1)), "last_successful_read": None}
            continue
        row = state[wallet]
        row["depth"] = max(1, depths.get(wallet, row.get("depth", 1)))
        row["interval_seconds"] = (6 * 3600 if incoming.get(wallet, 0) - outgoing.get(wallet, 0) > 50_000
                                   else INTERVAL)
        last = row.get("last_successful_read") or 0
        if newest.get(wallet, 0) > last or row.get("coverage_schema", COVERAGE_SCHEMA) != COVERAGE_SCHEMA:
            row["next_check"] = min(row.get("next_check", now_ts), now_ts)
        if row.get("next_check", now_ts + 1) <= now_ts:
            due.append({"wallet": wallet, **row})
    due.sort(key=lambda row: (row["next_check"], row["wallet"]))
    return due[:max(0, limit)], state


def record_refresh(state, wallet, now_ts, depth, *, error=None):
    row = state.setdefault(wallet, {})
    row.update(last_checked=now_ts, depth=depth)
    if error:
        row.update(last_error=str(error)[:200], next_check=now_ts + 3600)
    else:
        row.update(last_successful_read=now_ts, coverage_schema=COVERAGE_SCHEMA,
                   next_check=now_ts + row.get("interval_seconds", INTERVAL))
        row.pop("last_error", None)
