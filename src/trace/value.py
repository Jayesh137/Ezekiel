# src/trace/value.py
"""How much of HIS money reached each address. Pure.

The old frontier ranked a wallet by everything it had received from anyone, so
an exchange hot wallet outranked a quiet wallet holding his money. This ranks by
his share instead (a "haircut" model): a wallet passes on his money in the
proportion it holds it. Seeds (the config cluster) hold only his money; a
boundary (exchange, protocol, hub) passes on none of it, because what leaves a
custody pool is not traceably his — that gap belongs to the gap-crossers.

Shares are settled in depth order from the seeds and a transfer back to an
equal-or-shallower address is ignored, so money that goes out and comes back
can never inflate itself.
"""

from __future__ import annotations


def _low(a) -> str:
    return (a or "").strip().lower()


def his_money(edges, *, seeds, boundaries) -> dict[str, dict]:
    """address -> {in_usd, share, depth, unvalued, parents}; seeds excluded."""
    seeds = {_low(s) for s in seeds}
    boundaries = {_low(b) for b in boundaries} - seeds
    out_of: dict[str, list[dict]] = {}
    into: dict[str, list[dict]] = {}
    for e in edges or []:
        src, dst = _low(e.get("src")), _low(e.get("dst"))
        if not src or not dst or src == dst:
            continue
        out_of.setdefault(src, []).append(e)
        into.setdefault(dst, []).append(e)

    depth = {s: 0 for s in seeds}
    frontier = sorted(seeds)
    while frontier:
        following = []
        for addr in frontier:
            if addr in boundaries:
                continue
            for e in out_of.get(addr, []):
                dst = _low(e.get("dst"))
                if dst not in depth:
                    depth[dst] = depth[addr] + 1
                    following.append(dst)
        frontier = sorted(following)

    share = {s: 1.0 for s in seeds}
    result: dict[str, dict] = {}
    for addr in sorted((a for a in depth if a not in seeds), key=lambda a: (depth[a], a)):
        tainted = total = 0.0
        unvalued = 0
        parents: dict[str, float] = {}
        for e in into.get(addr, []):
            src = _low(e.get("src"))
            usd = e.get("amount_usd")
            from_shallower = src in depth and depth[src] < depth[addr]
            s = share.get(src, 0.0) if from_shallower else 0.0
            if usd is None:
                if s > 0:
                    unvalued += 1
                continue
            usd = float(usd)
            total += usd
            if s > 0:
                tainted += usd * s
                parents[src] = parents.get(src, 0.0) + usd * s
        if tainted <= 0 and not unvalued:
            continue
        own = (tainted / total) if total > 0 else 1.0
        share[addr] = 0.0 if addr in boundaries else min(1.0, own)
        result[addr] = {
            "in_usd": round(tainted, 2),
            "share": round(min(1.0, own), 4),
            "depth": depth[addr],
            "unvalued": unvalued,
            "parents": [p for p, _ in sorted(parents.items(), key=lambda kv: -kv[1])[:3]],
        }
    return result
