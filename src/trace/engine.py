# src/trace/engine.py
"""One run of the trace engine: follow his money until it reaches Hyperliquid.

Work is a set of small units — (address, source) — never "expand a wallet":

    hl        the address's Hyperliquid ledger, incrementally from a cursor
    classify  whole-chain activity (keyless Blockscout), so a busy address or a
              contract is a boundary BEFORE anything is spent sweeping it
    l1        the existing per-chain substrate sweep, for quiet wallets holding
              his money that nothing has swept

Each unit records its own result and error. One failing never blocks another,
and a partial read is progress (the 2026-10-04 rule). Order comes from how much
of HIS money reached an address (`value.his_money`), not from how much anyone
sent it. Spec: docs/superpowers/specs/2026-10-04-trace-engine-design.md.

All IO goes through injected callables so a run is testable offline.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime

from src.trace import hl_ledger, patterns, value

# Classes. Boundaries pass on none of his money and are never walked.
CLUSTER = "cluster"
SERVICE = "service"          # curated label or configured service
CEX_DEPOSIT = "cex_deposit"  # L1 address forwarding to an exchange hot wallet
HL_DEPOSIT = "hl_deposit"    # HyperCore address forwarding to an exchange hub
HUB = "hub"                  # HL account with hub-scale fan degree
CONTRACT = "contract"
BUSY = "busy"
QUIET = "quiet_eoa"
UNKNOWN = "unknown"
BOUNDARY_CLASSES = {SERVICE, CEX_DEPOSIT, HL_DEPOSIT, HUB, CONTRACT, BUSY}

# HyperCore system addresses (0x20…<token index>, 0x2222…): moves to them are
# transfers to HyperEVM or Circle withdrawals, already decoded elsewhere.
SYSTEM_PREFIXES = ("0x20000000000000000000000000000000000000", "0x2222222222")

SINGLE_PURPOSE_TXS = 100

DEFAULTS = {
    "hl_reads": 60,            # ledger units per run (HL weight is paced too)
    "classify_reads": 25,      # Blockscout activity readings per run
    "l1_sweeps": 6,            # substrate sweeps per run
    "seconds": 240,            # whole run; step timeout is larger (rule: order)
    "min_reach_usd": 10_000,   # an HL account reached by at least this much of his money
    "hl_refresh_s": 6 * 3600,  # cluster/linked ledgers re-read this often
}


def _low(a) -> str:
    return (a or "").strip().lower()


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def is_system(address: str) -> bool:
    return _low(address).startswith(SYSTEM_PREFIXES)


def classify(address: str, record: dict, *, cluster, services, inferred, activity,
             l1_seen: bool = False, has_code: bool = False) -> str:
    """The class of one address from everything already known about it. Pure.

    `activity` is the list of whole-chain readings for the address (rule 9:
    nothing unmeasured is ever quiet). For an address seen only inside
    Hyperliquid, its own ledger read and found not to be a hub is the measure.
    An address seen on L1 needs an L1 reading: HL rows alone (airdrops land on
    token contracts too — 0x5d3a1ff2, 2026-10-04) never make it a person.
    """
    a = _low(address)
    if a in cluster:
        return CLUSTER
    if a in services or is_system(a):
        return SERVICE
    hl = record.get("hl") or {}
    if hl.get("hub"):
        return HUB
    if hl.get("deposit_hub"):
        return HL_DEPOSIT
    if a in inferred:
        return CEX_DEPOSIT
    if has_code:
        return CONTRACT
    readings = [r for r in activity or [] if isinstance(r, dict)]
    if any(r.get("is_contract") for r in readings):
        return CONTRACT
    from src.chain.activity import is_busy
    if any(is_busy(r) for r in readings):
        return BUSY
    if readings or (not l1_seen and hl.get("read_ok") and hl.get("rows")):
        return QUIET
    return UNKNOWN


def priority(info: dict, cls: str, record: dict, now_ts: float) -> float:
    """Where the next unit is most likely to lead to an HL account of his."""
    usd = float(info.get("in_usd") or 0)
    money = min(1.0, math.log10(1 + usd) / 9) + (0.05 if info.get("unvalued") else 0)
    weight = {QUIET: 1.0, UNKNOWN: 0.7}.get(cls, 0.0)
    if (record.get("hl") or {}).get("rows"):
        weight *= 1.5
    return round(money * weight * (0.9 ** max(0, int(info.get("depth") or 1) - 1)), 6)


def l1_edges(records) -> list[dict]:
    """Substrate records as the minimal edge shape the engine reads."""
    out = []
    for r in records or []:
        if r.get("spam"):
            continue
        out.append({"id": r.get("id"), "src": _low(r.get("src")), "dst": _low(r.get("dst")),
                    "amount_usd": r.get("amount_usd"), "ts": int(r.get("ts") or 0),
                    "chain": r.get("chain"), "asset": r.get("asset")})
    return out


def reverse(edges) -> list[dict]:
    return [{**e, "src": e["dst"], "dst": e["src"]} for e in edges]


def run(*, cluster, registry: dict, hl_store: dict, previous: dict | None,
        l1_records_for, hl_post, activity, sweep, services: set, inferred: set,
        cex_hot: set = frozenset(), code: dict | None = None,
        already_swept: set = frozenset(), budgets: dict | None = None, now_ts: float | None = None,
        clock=time.monotonic) -> dict:
    """One bounded run. Mutates `registry` and `hl_store`; returns the report.

    l1_records_for(addresses) -> {address: [substrate records]}
    hl_post(body) -> list            (raises on failure; paced by the caller)
    activity.get(address, chain) / .cached(address, chain) -> reading | None
                                     (chain.activity.ActivityCache)
    sweep(address) -> sweep result   (chain.collect.sweep_wallet, bound)
    code: {"chain:address": has bytecode}   (labels/code_cache.json)
    already_swept: wallets the substrate already holds a sweep for — read, not re-swept
    """
    b = {**DEFAULTS, **(budgets or {})}
    now_ts = now_ts if now_ts is not None else time.time()
    started = clock()
    cluster = {_low(c) for c in cluster}
    spent = {"hl": 0, "classify": 0, "l1": 0}
    errors: list[dict] = []

    def out_of_time() -> bool:
        return clock() - started >= b["seconds"]

    def rec(addr: str) -> dict:
        return registry.setdefault(addr, {"first_seen": _now_iso(now_ts)})

    code = code or {}
    already_swept = {_low(a) for a in already_swept}
    known: set = set()

    def swept_addresses() -> set:
        mine = {a for a, r in registry.items() if (r.get("l1") or {}).get("swept_at")}
        return cluster | mine | (already_swept & (known | set(registry)))

    def all_edges() -> list[dict]:
        edges = []
        for rows in l1_records_for(sorted(swept_addresses())).values():
            edges.extend(l1_edges(rows))
        for rows in hl_store.values():
            edges.extend(rows)
        seen, unique = set(), []
        for e in edges:
            if e.get("id") in seen:
                continue
            seen.add(e.get("id"))
            unique.append(e)
        return unique

    chain_index: dict[str, set] = {}
    inferred = {_low(a) for a in inferred}

    def infer(edges) -> None:
        # A wallet swept THIS run can be a deposit address; the graph's own
        # inference only runs after, so without this it would be walked as a
        # wallet for a run first. Same rule as everywhere: labels.infer_*.
        from src.chain.labels import infer_deposit_addresses
        if not cex_hot:
            return
        l1 = [e for e in edges if e.get("chain") != hl_ledger.CHAIN]
        inferred.update(infer_deposit_addresses(l1, cex_hot))
        # Deposit by deposit as well: the rule above calls a deposit address
        # used for weeks a wallet (patterns.l1_deposit_hot).
        for addr in swept_addresses() - cluster:
            if addr not in inferred and patterns.l1_deposit_hot(l1, addr, hot=cex_hot):
                inferred.add(addr)

    def index(edges) -> None:
        chain_index.clear()
        for e in edges:
            c = e.get("chain")
            if not c or c == hl_ledger.CHAIN:
                continue
            chain_index.setdefault(e["src"], set()).add(c)
            chain_index.setdefault(e["dst"], set()).add(c)

    def chains_of(addr: str, _edges=None) -> list[str]:
        return sorted(chain_index.get(addr, ()))

    def class_of(addr: str, _edges=None) -> str:
        chains = chains_of(addr)
        readings = [activity.cached(addr, c) for c in chains]
        return classify(addr, registry.get(addr) or {}, cluster=cluster, services=services,
                        inferred=inferred, activity=readings, l1_seen=bool(chains),
                        has_code=any(code.get(f"{c}:{addr}") is True for c in chains))

    def read_hl(addr: str) -> None:
        r = rec(addr)
        hl = r.setdefault("hl", {})
        cursor = int(hl.get("cursor_ms") or 0)
        rows, err, complete = hl_ledger.read(addr, hl_post, start_ms=cursor, max_pages=2)
        spent["hl"] += 1
        hl["read_at"] = _now_iso(now_ts)
        if err and not rows:
            hl["error"] = err
            hl["read_ok"] = False
            errors.append({"unit": "hl", "address": addr, "error": err})
            return
        stored = hl_store.get(addr, [])
        hl["read_ok"] = True
        hl.pop("error", None)
        hl["rows"] = int(hl.get("rows") or 0) + len(rows)
        if rows:
            hl["cursor_ms"] = max(int(x.get("time") or 0) for x in rows) + 1
        if not complete:
            hl["incomplete"] = True
            if err:
                errors.append({"unit": "hl", "address": addr, "error": err, "partial": True})
        else:
            hl.pop("incomplete", None)
        if addr not in cluster and (patterns.is_hub(rows, addr) or hl["rows"] >= patterns.SATURATED_ROWS):
            hl["hub"] = True           # summary only: a hub's ledger is never stored
            hl_store.pop(addr, None)
            return
        hub = patterns.deposit_hub(rows, addr) if addr not in cluster else None
        if hub:
            hl["deposit_hub"] = hub
        fresh = hl_ledger.normalise(rows, addr)
        ids = {e["id"] for e in stored}
        hl_store[addr] = stored + [e for e in fresh if e["id"] not in ids]

    def due_hl(addr: str) -> bool:
        hl = (registry.get(addr) or {}).get("hl") or {}
        if not hl.get("read_at"):
            return True
        if hl.get("hub"):
            return False
        try:
            last = datetime.fromisoformat(hl["read_at"]).timestamp()
        except (TypeError, ValueError):
            return True
        return now_ts - last >= b["hl_refresh_s"] or hl.get("incomplete") or not hl.get("read_ok")

    # 1. His own wallets first: their HL ledgers name his first HL-native hop.
    for addr in sorted(cluster):
        if spent["hl"] >= b["hl_reads"] or out_of_time():
            break
        if due_hl(addr):
            read_hl(addr)

    # 2-4. Rank by his money, then spend each source's budget on the best units.
    for _pass in range(2):
        if out_of_time():
            break
        edges = all_edges()
        index(edges)
        infer(edges)
        boundaries = {a for a in {e["src"] for e in edges} | {e["dst"] for e in edges}
                      if class_of(a, edges) in BOUNDARY_CLASSES}
        money = value.his_money(edges, seeds=cluster, boundaries=boundaries)
        funding = value.his_money(reverse(edges), seeds=cluster, boundaries=boundaries)
        known.update(a for a in (*money, *funding) if a not in boundaries)
        best: dict[str, float] = {}
        for side in (money, funding):
            for a, info in side.items():
                if a not in boundaries:
                    p = priority(info, class_of(a), registry.get(a) or {}, now_ts)
                    best[a] = max(best.get(a, 0.0), p)
        ranked = sorted(((p, a) for a, p in best.items()), reverse=True)
        for _p, addr in ranked:
            if out_of_time():
                break
            if spent["hl"] < b["hl_reads"] and due_hl(addr):
                read_hl(addr)
            if spent["classify"] < b["classify_reads"] and class_of(addr, edges) == UNKNOWN:
                for chain in chains_of(addr, edges):
                    if spent["classify"] >= b["classify_reads"]:
                        break
                    spent["classify"] += 1
                    if activity.get(addr, chain) is None:
                        why = ("out of time" if getattr(activity, "out_of_time", False)
                               else "no reading (no public reader for this chain, or it failed)")
                        errors.append({"unit": "classify", "address": addr, "chain": chain,
                                       "error": f"unmeasured: {why}"})
            r = registry.get(addr) or {}
            if (spent["l1"] < b["l1_sweeps"] and class_of(addr, edges) == QUIET
                    and not (r.get("l1") or {}).get("swept_at") and chains_of(addr, edges)):
                spent["l1"] += 1
                result = sweep(addr) or {}
                l1 = rec(addr).setdefault("l1", {})
                read = [c for c in (result.get("chains") or {})
                        if c not in (result.get("degraded_sources") or [])
                        and c not in (result.get("unsupported_sources") or [])]
                if read:
                    l1["swept_at"] = _now_iso(now_ts)
                    l1["chains_read"] = read
                l1["degraded"] = list(result.get("degraded_sources") or [])
                if l1["degraded"]:
                    errors.append({"unit": "l1", "address": addr, "error": "could not read "
                                   + ", ".join(l1["degraded"])})

    # 5. Findings.
    edges = all_edges()
    index(edges)
    infer(edges)
    classes = {a: class_of(a, edges) for a in {e["src"] for e in edges} | {e["dst"] for e in edges}}
    boundaries = {a for a, c in classes.items() if c in BOUNDARY_CLASSES}
    money = value.his_money(edges, seeds=cluster, boundaries=boundaries)
    funding = value.his_money(reverse(edges), seeds=cluster, boundaries=boundaries)
    for a, cls in classes.items():
        if a in registry or a in money:
            rec(a)["class"] = cls
    for a, info in money.items():
        rec(a)["his_money"] = {k: info[k] for k in ("in_usd", "share", "depth", "unvalued", "parents")}

    # Payees must be MEASURED single-purpose: quiet, with a small whole-chain
    # history (0x734c9213: 5 transactions in its life).
    def single_purpose(addr: str) -> bool:
        readings = [activity.cached(addr, c) for c in chains_of(addr)]
        readings = [r for r in readings if isinstance(r, dict)]
        return bool(readings) and all(int(r.get("txs") or 0) <= SINGLE_PURPOSE_TXS
                                      for r in readings)
    payees = {a for a, c in classes.items() if c == QUIET and single_purpose(a)}
    links = [{**row, "kind": "shared_payee"}
             for row in patterns.shared_payees(edges, cluster=cluster, quiet=payees,
                                               boundaries=boundaries)]
    for addr, r in sorted(registry.items()):
        hub = (r.get("hl") or {}).get("deposit_hub")
        if not hub:
            continue
        paid = [e for e in edges if e["dst"] == addr and e["src"] in cluster]
        if not paid:
            continue
        cluster_usd = round(sum(float(p.get("amount_usd") or 0) for p in paid), 2)
        links.extend({"kind": "shared_hl_deposit", "wallet": e["src"], "via": addr, "hub": hub,
                      "outsider_usd": e.get("amount_usd"), "cluster_usd": cluster_usd,
                      "ts": e.get("ts")}
                     for e in edges
                     if e["dst"] == addr and e["src"] not in cluster and e["src"] != hub)

    # Quiet wallets that FUNDED him, and where their own money came from: a
    # fresh wallet filled from an exchange and emptied into his is the shape a
    # new wallet of his takes (0x68797748: Binance 16 -> it -> 0xf078969e, $6M).
    funders = []
    for a, info in funding.items():
        if info["depth"] != 1 or a in boundaries or float(info["in_usd"]) < b["min_reach_usd"]:
            continue
        sources = sorted({e["src"] for e in edges if e["dst"] == a and e["src"] not in cluster})
        funders.append({"address": a, "paid_him_usd": info["in_usd"], "class": classes.get(a),
                        "swept": bool(((registry.get(a) or {}).get("l1") or {}).get("swept_at")),
                        "funded_by": [{"address": s, "class": classes.get(s)} for s in sources[:5]]})
    funders.sort(key=lambda row: -float(row["paid_him_usd"]))

    reached = []
    for a, info in money.items():
        hl = (registry.get(a) or {}).get("hl") or {}
        if a in cluster or a in boundaries or not hl.get("rows"):
            continue
        if float(info["in_usd"]) < b["min_reach_usd"] and not info.get("unvalued"):
            continue
        reached.append({"address": a, "in_usd": info["in_usd"], "share": info["share"],
                        "depth": info["depth"], "parents": info["parents"],
                        "hl_rows": hl.get("rows"), "class": classes.get(a)})
    reached.sort(key=lambda row: -float(row["in_usd"]))

    paid_by_him = {e["dst"] for e in edges if e["src"] in cluster}
    deposit_addresses = [
        {"address": a, "kind": c, "hub": ((registry.get(a) or {}).get("hl") or {}).get("deposit_hub")}
        for a, c in sorted(classes.items()) if c in (CEX_DEPOSIT, HL_DEPOSIT) and a in paid_by_him]
    stops = sorted(({"address": a, "class": classes.get(a), "in_usd": money[a]["in_usd"]}
                    for a in boundaries if a in money), key=lambda r: -float(r["in_usd"]))

    prev_keys = {(row.get("kind"), row.get("wallet"), row.get("via"))
                 for row in (previous or {}).get("links") or []}
    prev_reached = {row.get("address") for row in (previous or {}).get("reached_hl_accounts") or []}
    return {
        "computed_at": _now_iso(now_ts),
        "units": spent,
        "budgets": {k: b[k] for k in ("hl_reads", "classify_reads", "l1_sweeps", "seconds")},
        "elapsed_seconds": round(clock() - started, 1),
        "errors": errors[:200],
        "addresses_known": len(registry),
        "edges": len(edges),
        "reached_hl_accounts": reached[:200],
        "new_reached": [row for row in reached if row["address"] not in prev_reached],
        "funders": funders[:200],
        "links": links,
        "new_links": [row for row in links
                      if (row.get("kind"), row.get("wallet"), row.get("via")) not in prev_keys],
        "deposit_addresses": deposit_addresses,
        "boundaries": stops[:100],
    }
