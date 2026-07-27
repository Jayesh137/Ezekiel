# src/transfer_graph.py
"""Persistent, bounded transfer graph for linked-wallet discovery.

The three detection vectors (L1 tracing, HL-native ledger analysis, deposit
correlation) each produced their own findings file with its own shape, so a
wallet reachable by two weak paths looked no stronger than one reachable by a
single hop. This module normalises every transfer into one edge type, walks the
graph outward from the target under an explicit budget, and scores each
discovered wallet from the accumulated evidence.

Design rules that matter:

* A transfer is NOT ownership. One wallet sending funds to another is evidence of
  a relationship, nothing more. Classification is deliberately graded, and
  `MIGRATION_CANDIDATE` requires corroboration from an independent vector
  (behavioural similarity, or HL-native two-way flow, or amount correlation) —
  never a transfer alone.
* Negative evidence is first-class. Exchanges, bridges, contracts and mixers all
  receive funds from the target; a high fan-in/fan-out address is a service, and
  services can never be linked wallets no matter how much flows through them.
* Every edge keeps enough provenance to audit the conclusion after the fact.

The pure functions (normalise_*, build_graph, classify_node, score_confidence)
take plain data and do no I/O, matching the pattern in ledger_analyzer.py and
correlator.py, so the traversal and scoring are unit-testable without network.
"""

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    DATA_DIR,
    load_all_records,
    load_config,
    now_ms,
    save_latest,
    write_cursor,
)

# --- chains / sources -----------------------------------------------------------

CHAIN_ARBITRUM = "arbitrum"
CHAIN_HYPERLIQUID = "hyperliquid"

SRC_L1 = "l1_transfer"
SRC_HL_LEDGER = "hl_ledger"
SRC_BRIDGE_DEPOSIT = "bridge_deposit"
SRC_BRIDGE_WITHDRAW = "bridge_withdraw"
SRC_GAS_FUNDING = "gas_funding"

# --- classifications (ordered weakest -> strongest) -----------------------------

CLASS_SERVICE = "SERVICE"                      # exchange / bridge / contract / mixer
CLASS_DIRECT_RECIPIENT = "DIRECT_RECIPIENT"    # received funds, nothing more known
CLASS_OPERATIONAL = "OPERATIONAL_COUNTERPARTY"  # repeated/two-way relationship
CLASS_POSSIBLE_LINKED = "POSSIBLE_LINKED_WALLET"
CLASS_MIGRATION_CANDIDATE = "MIGRATION_CANDIDATE"

CLASS_ORDER = {
    CLASS_SERVICE: 0,
    CLASS_DIRECT_RECIPIENT: 1,
    CLASS_OPERATIONAL: 2,
    CLASS_POSSIBLE_LINKED: 3,
    CLASS_MIGRATION_CANDIDATE: 4,
}

# --- traversal budget defaults (overridable via config.json) --------------------

DEFAULTS = {
    "max_depth": 3,
    "max_nodes": 300,
    "max_expansions": 40,     # Etherscan-backed hop expansions per run
    "time_budget_seconds": 150,
    "min_edge_usd": 1000.0,
    "dust_usd": 1.0,
    # Fan degree at/above which an address is treated as a service, not a wallet.
    "service_fanout": 25,
    "service_fanin": 25,
}

# A wallet that both sends to and receives from the target this many times is
# operationally entangled with it rather than a one-off recipient.
REPEATED_TRANSFER_MIN = 3
# Withdrawal -> new-wallet deposit/trading within this window is suggestive.
FAST_REENTRY_HOURS = 72.0
# Transfer evidence ages: a wallet funded three years ago is weaker evidence of a
# migration happening now. Flat for FRESH_DAYS, then linear to FLOOR — the floor
# keeps a genuinely linked old wallet visible rather than erasing it. Only the
# transfer-derived score decays; behaviour and address reuse do not.
RECENCY_FRESH_DAYS = 90.0
RECENCY_DECAY_DAYS = 365.0
RECENCY_FLOOR = 0.4
# Split-transfer detection: fragments summing to within this fraction of an exit.
SPLIT_TOLERANCE = 0.05


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _iso(ts_seconds: float | int | None) -> str | None:
    if not ts_seconds:
        return None
    try:
        return datetime.fromtimestamp(float(ts_seconds), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def edge_id(src: str, dst: str, chain: str, ref: str, ts: int = 0) -> str:
    """Stable identity so re-ingesting the same transfer never double-counts.

    Keyed on chain + reference + direction, and deliberately NOT on the timestamp.
    One transfer reaches this module through several paths — the raw
    l1_transactions table carries the block timeStamp while a fund_flows finding
    carries its own detected_at — so including ts minted two ids for one real
    movement and doubled `transfer_count`, inflating the "repeated transfers"
    signal from a single transaction.

    Direction stays in the key because one tx hash can carry several token
    transfers between different pairs, and one ledger hash appears on both sides
    of an internal transfer.
    """
    raw = f"{chain}|{(ref or '').lower()}|{src.lower()}|{dst.lower()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# --- normalisation --------------------------------------------------------------

def normalise_l1_transfer(tx: dict, decimals: int = 6) -> dict | None:
    """Normalise an Etherscan tokentx row into a graph edge."""
    src = (tx.get("from") or "").lower()
    dst = (tx.get("to") or "").lower()
    if not src or not dst or src == dst:
        return None
    try:
        amount = int(tx.get("value", 0) or 0) / (10 ** decimals)
        ts = int(tx.get("timeStamp", 0) or 0)
    except (TypeError, ValueError):
        return None
    ref = tx.get("hash", "")
    return {
        "id": edge_id(src, dst, CHAIN_ARBITRUM, ref, ts),
        "src": src,
        "dst": dst,
        "chain": CHAIN_ARBITRUM,
        "asset": tx.get("tokenSymbol") or "USDC",
        "amount_usd": round(amount, 2),
        "ref": ref,
        "ts": ts,
        "timestamp": _iso(ts),
        "discovery_source": SRC_L1,
    }


def normalise_hl_ledger_entry(entry: dict) -> dict | None:
    """Normalise a Hyperliquid non-funding ledger update into a graph edge.

    Covers send / internalTransfer / spotTransfer (wallet-to-wallet inside HL) and
    deposit / withdraw (bridge crossings), which the L1 tracer cannot see as
    wallet-to-wallet events.
    """
    delta = entry.get("delta") or {}
    dtype = delta.get("type")
    try:
        ts = int(entry.get("time", 0) or 0)
    except (TypeError, ValueError):
        return None
    ref = entry.get("hash", "")

    def _usd() -> float:
        for key in ("usdc", "usdcValue", "amount"):
            v = delta.get(key)
            if v not in (None, "", "0", "0.0"):
                try:
                    return abs(float(v))
                except (TypeError, ValueError):
                    continue
        return 0.0

    if dtype in ("send", "internalTransfer", "spotTransfer"):
        src = (delta.get("user") or "").lower()
        dst = (delta.get("destination") or "").lower()
        if not src or not dst or src == dst:
            return None
        return {
            "id": edge_id(src, dst, CHAIN_HYPERLIQUID, ref, ts),
            "src": src,
            "dst": dst,
            "chain": CHAIN_HYPERLIQUID,
            "asset": delta.get("token") or "USDC",
            "amount_usd": round(_usd(), 2),
            "ref": ref,
            "ts": ts // 1000,
            "timestamp": _iso(ts // 1000),
            "discovery_source": SRC_HL_LEDGER,
            "ledger_type": dtype,
        }

    if dtype in ("deposit", "withdraw"):
        # Bridge crossings have no HL counterparty wallet; they are recorded
        # against the account itself so entry/exit timing can be correlated.
        return {
            "id": edge_id(dtype, dtype, CHAIN_HYPERLIQUID, ref, ts),
            "src": dtype,
            "dst": dtype,
            "chain": CHAIN_HYPERLIQUID,
            "asset": "USDC",
            "amount_usd": round(_usd(), 2),
            "ref": ref,
            "ts": ts // 1000,
            "timestamp": _iso(ts // 1000),
            "discovery_source": (SRC_BRIDGE_DEPOSIT if dtype == "deposit"
                                 else SRC_BRIDGE_WITHDRAW),
            "ledger_type": dtype,
            "bridge_event": True,
        }
    return None


def normalise_gas_funding(wallet: str, funder: str, ts: int,
                          amount_eth: float = 0.0) -> dict:
    """A first-gas relationship: whoever paid a fresh wallet's first gas.

    Kept as an edge (not just a linkage flag) so it shows up in the path a
    conclusion was drawn from.
    """
    src, dst = funder.lower(), wallet.lower()
    return {
        "id": edge_id(src, dst, CHAIN_ARBITRUM, f"gas:{dst}", ts),
        "src": src,
        "dst": dst,
        "chain": CHAIN_ARBITRUM,
        "asset": "ETH",
        "amount_usd": 0.0,
        "amount_native": amount_eth,
        "ref": f"gas:{dst}",
        "ts": ts,
        "timestamp": _iso(ts),
        "discovery_source": SRC_GAS_FUNDING,
    }


def dedupe_edges(edges: list[dict]) -> list[dict]:
    """Collapse re-ingested duplicates by edge id, keeping the widest seen range.

    The collectors re-read overlapping windows every run, so the same transfer
    arrives many times; without this the confidence signals that count repeated
    transfers would inflate from a single real movement.
    """
    by_id: dict[str, dict] = {}
    for e in edges:
        if not e:
            continue
        prev = by_id.get(e["id"])
        if prev is None:
            by_id[e["id"]] = dict(e)
            continue
        prev["ts"] = min(prev["ts"], e["ts"]) if prev["ts"] and e["ts"] else (prev["ts"] or e["ts"])
        prev["timestamp"] = _iso(prev["ts"])
    return sorted(by_id.values(), key=lambda e: (e["ts"], e["id"]))


# --- service detection ----------------------------------------------------------

def detect_services(edges: list[dict], known_services: set,
                    fanout: int = DEFAULTS["service_fanout"],
                    fanin: int = DEFAULTS["service_fanin"]) -> dict:
    """Identify addresses that behave like infrastructure rather than wallets.

    Two sources: explicitly configured addresses (bridge, known CEX deposit
    addresses), and observed many-to-many behaviour. An exchange receives from
    hundreds of unrelated wallets; treating one as a "linked wallet" because the
    target withdrew to it is the classic false positive this guards against.
    """
    out_deg: dict[str, set] = {}
    in_deg: dict[str, set] = {}
    for e in edges:
        if e.get("bridge_event"):
            continue
        out_deg.setdefault(e["src"], set()).add(e["dst"])
        in_deg.setdefault(e["dst"], set()).add(e["src"])

    services = {}
    for addr in set(out_deg) | set(in_deg):
        a = addr.lower()
        if a in known_services:
            services[a] = "configured service address (exchange/bridge/contract)"
            continue
        o, i = len(out_deg.get(addr, ())), len(in_deg.get(addr, ()))
        if o >= fanout and i >= fanin:
            services[a] = f"many-to-many flow ({i} senders, {o} recipients)"
        elif i >= fanin * 2:
            services[a] = f"high fan-in ({i} distinct senders)"
        elif o >= fanout * 2:
            services[a] = f"high fan-out ({o} distinct recipients)"
    return services


# --- confidence scoring ---------------------------------------------------------

def recency_factor(age_days: float | None) -> float:
    """Weight for how recently the transfer relationship was active.

    A wallet the target funded three years ago is weaker evidence of a migration
    happening now than one funded last week. Decay applies only to the
    transfer-derived portion of the score: behavioural similarity and address
    reuse are independent of when money moved and are not aged.

    Flat for FRESH_DAYS, then linear to a floor — the floor keeps a genuinely
    linked old wallet on the board rather than erasing it.
    """
    if age_days is None:
        return 1.0
    if age_days <= RECENCY_FRESH_DAYS:
        return 1.0
    decayed = 1.0 - (age_days - RECENCY_FRESH_DAYS) / RECENCY_DECAY_DAYS
    return max(RECENCY_FLOOR, min(1.0, decayed))


def score_confidence(ev: dict) -> tuple[float, list[str]]:
    """Combine evidence into a 0-1 linkage confidence plus readable reasons.

    Pure: `ev` is a plain dict of already-measured facts. Negative evidence
    short-circuits — a service address is never a linked wallet.

    Two buckets. `relationship` is everything derived from money moving, and is
    aged by recency_factor(). `corroboration` is independent evidence — behaviour,
    amount correlation, address reuse, gas funding — which does not decay.
    """
    reasons: list[str] = []

    if ev.get("is_service"):
        return 0.0, [f"Excluded: {ev.get('service_reason', 'service address')}"]

    relationship = 0.0
    corroboration = 0.0

    if ev.get("direct_from_target"):
        relationship += 0.15
        reasons.append("Received funds directly from the target wallet")
    if ev.get("funded_target"):
        relationship += 0.08
        reasons.append("Sent funds to the target wallet")

    transfers = int(ev.get("transfer_count", 0) or 0)
    if transfers >= REPEATED_TRANSFER_MIN:
        relationship += 0.15
        reasons.append(f"Repeated transfers ({transfers} separate movements)")

    if ev.get("bidirectional"):
        relationship += 0.20
        reasons.append("Two-way flow — funds move both to and from this wallet")

    if ev.get("hl_native"):
        relationship += 0.12
        reasons.append("Transfer happened entirely inside Hyperliquid (no L1 trace)")

    if ev.get("shared_funder"):
        corroboration += 0.12
        reasons.append("Shares the target's original funding source")
    if ev.get("shared_deposit_address"):
        corroboration += 0.18
        reasons.append("Sends to the same deposit address as the target (address reuse)")
    if ev.get("gas_funded_by_target"):
        corroboration += 0.15
        reasons.append("First gas paid by the target wallet")

    gap = ev.get("reentry_gap_hours")
    if gap is not None and gap <= FAST_REENTRY_HOURS:
        corroboration += 0.15
        reasons.append(f"Began depositing/trading {gap:.1f}h after a target exit")

    if ev.get("amount_correlation"):
        conf = float(ev.get("amount_correlation") or 0)
        corroboration += 0.15 * min(1.0, conf)
        detail = "split across multiple transfers" if ev.get("split_transfer") else "single amount"
        reasons.append(f"Exit amount re-appears as a deposit ({detail}, "
                       f"{conf * 100:.0f}% match confidence)")

    behavioural = ev.get("behavioural_score")
    if behavioural is not None:
        b = float(behavioural)
        if b >= 0.65:
            corroboration += 0.25 * min(1.0, (b - 0.65) / 0.35 + 0.4)
            reasons.append(f"Trades like the target (behavioural similarity {b * 100:.0f}%)")
        elif b > 0:
            reasons.append(f"Behavioural similarity only {b * 100:.0f}% — weak on its own")

    if ev.get("trades_on_hl"):
        corroboration += 0.08
        reasons.append("Actively trading on Hyperliquid after receiving funds")

    age = ev.get("age_days")
    factor = recency_factor(age)
    if factor < 1.0:
        relationship *= factor
        reasons.append(f"Last movement {age:.0f} days ago — transfer evidence "
                       f"aged to {factor * 100:.0f}%")

    score = relationship + corroboration

    # Distance penalty: every extra hop admits an unrelated intermediary.
    depth = int(ev.get("depth", 1) or 1)
    if depth > 1:
        score *= max(0.35, 1.0 - 0.25 * (depth - 1))
        reasons.append(f"Reached at {depth} hops from target — confidence discounted")

    if ev.get("only_single_transfer") and not ev.get("behavioural_score"):
        reasons.append("Single transfer with no corroborating evidence — "
                       "relationship unproven, NOT treated as same owner")

    return round(max(0.0, min(1.0, score)), 4), reasons


def classify_node(ev: dict, confidence: float) -> str:
    """Grade a wallet from its evidence. Deliberately conservative.

    MIGRATION_CANDIDATE requires an independent corroborating vector, so a large
    transfer alone can never reach it.
    """
    if ev.get("is_service"):
        return CLASS_SERVICE

    corroborated = bool(
        (ev.get("behavioural_score") or 0) >= 0.65
        or ev.get("amount_correlation")
        or ev.get("shared_deposit_address")
        or ev.get("gas_funded_by_target")
        or (ev.get("bidirectional") and ev.get("hl_native"))
    )

    if confidence >= 0.60 and corroborated:
        return CLASS_MIGRATION_CANDIDATE
    if confidence >= 0.40 and corroborated:
        return CLASS_POSSIBLE_LINKED
    if ev.get("bidirectional") or int(ev.get("transfer_count", 0) or 0) >= REPEATED_TRANSFER_MIN:
        return CLASS_OPERATIONAL
    return CLASS_DIRECT_RECIPIENT


# --- split-transfer correlation -------------------------------------------------

def find_split_correlation(exit_amount: float, inbound: list[dict],
                           tolerance: float = SPLIT_TOLERANCE,
                           max_parts: int = 4) -> dict | None:
    """Detect an exit re-entering as several smaller deposits.

    A migrator who splits $1.2M into four ~$300k deposits defeats the
    single-amount matcher in correlator.py. Greedy largest-first accumulation is
    enough here and stays linear — this runs per candidate, not per pair.
    """
    if exit_amount <= 0 or not inbound:
        return None
    parts = sorted((d for d in inbound if float(d.get("amount_usd", 0)) > 0),
                   key=lambda d: -float(d["amount_usd"]))[:max_parts * 3]

    best = None
    for start in range(len(parts)):
        total = 0.0
        used = []
        for d in parts[start:]:
            amt = float(d["amount_usd"])
            if total + amt > exit_amount * (1 + tolerance):
                continue
            total += amt
            used.append(d)
            if len(used) > max_parts:
                break
            diff = abs(exit_amount - total) / exit_amount
            if diff <= tolerance and len(used) >= 2:
                cand = {"parts": len(used), "total_usd": round(total, 2),
                        "diff_pct": round(diff * 100, 3),
                        "confidence": round(1.0 - diff / tolerance, 4),
                        "refs": [u.get("ref") for u in used]}
                if best is None or cand["confidence"] > best["confidence"]:
                    best = cand
    return best


# --- graph construction ---------------------------------------------------------

def build_graph(edges: list[dict], target: str, *,
                known_services: set | None = None,
                behavioural: dict | None = None,
                hl_active: set | None = None,
                linkage: dict | None = None,
                correlations: dict | None = None,
                max_depth: int = DEFAULTS["max_depth"],
                max_nodes: int = DEFAULTS["max_nodes"],
                dust_usd: float = DEFAULTS["dust_usd"],
                now_ts: float | None = None,
                expansion: dict | None = None) -> dict:
    """Walk outward from the target, classifying and scoring every wallet reached.

    Pure function — every external input is passed in, so traversal and scoring
    are fully testable offline.
    """
    target = target.lower()
    now_ts = now_ts if now_ts is not None else time.time()
    known_services = {a.lower() for a in (known_services or set())}
    behavioural = {k.lower(): v for k, v in (behavioural or {}).items()}
    hl_active = {a.lower() for a in (hl_active or set())}
    linkage = {k.lower(): v for k, v in (linkage or {}).items()}
    correlations = {k.lower(): v for k, v in (correlations or {}).items()}

    edges = dedupe_edges(edges)
    services = detect_services(edges, known_services)

    # Adjacency over real wallet-to-wallet movement only.
    adj: dict[str, list[dict]] = {}
    wallet_edges = []
    for e in edges:
        if e.get("bridge_event"):
            continue
        if float(e.get("amount_usd", 0)) < dust_usd and e["discovery_source"] != SRC_GAS_FUNDING:
            continue  # address-poisoning dust moves no funds
        wallet_edges.append(e)
        adj.setdefault(e["src"], []).append(e)
        adj.setdefault(e["dst"], []).append(e)

    # BFS from the target. Services are recorded but never expanded through:
    # everything is reachable via an exchange, so traversing one would make the
    # graph meaningless.
    depths: dict[str, int] = {target: 0}
    parents: dict[str, tuple[str, dict]] = {}
    order: list[str] = []
    frontier = [target]
    while frontier and len(depths) < max_nodes:
        nxt = []
        for node in frontier:
            if depths[node] >= max_depth:
                continue
            if node != target and node.lower() in services:
                continue
            for e in adj.get(node, []):
                other = e["dst"] if e["src"] == node else e["src"]
                if other in depths or other == target:
                    continue
                depths[other] = depths[node] + 1
                parents[other] = (node, e)
                order.append(other)
                nxt.append(other)
                if len(depths) >= max_nodes:
                    break
            if len(depths) >= max_nodes:
                break
        frontier = nxt

    def path_to(addr: str) -> list[str]:
        path = [addr]
        seen = {addr}
        cur = addr
        while cur in parents:
            cur = parents[cur][0]
            if cur in seen:
                break
            seen.add(cur)
            path.append(cur)
        return list(reversed(path))

    nodes = []
    for addr in order:
        incident = [e for e in wallet_edges if e["src"] == addr or e["dst"] == addr]
        from_target = [e for e in incident if e["src"] == target and e["dst"] == addr]
        to_target = [e for e in incident if e["src"] == addr and e["dst"] == target]
        gas = [e for e in incident
               if e["discovery_source"] == SRC_GAS_FUNDING and e["dst"] == addr]

        out_usd = round(sum(float(e["amount_usd"]) for e in from_target), 2)
        in_usd = round(sum(float(e["amount_usd"]) for e in to_target), 2)
        stamps = [e["ts"] for e in incident if e["ts"]]
        link = linkage.get(addr, {})
        corr = correlations.get(addr, {})
        age_days = ((now_ts - max(stamps)) / 86400.0) if stamps else None

        ev = {
            "depth": depths[addr],
            "is_service": addr in services,
            "service_reason": services.get(addr),
            "direct_from_target": bool(from_target),
            "funded_target": bool(to_target),
            "bidirectional": bool(from_target and to_target),
            "transfer_count": len(incident),
            "only_single_transfer": len(incident) == 1,
            "hl_native": any(e["chain"] == CHAIN_HYPERLIQUID for e in incident),
            "shared_funder": bool(link.get("shared_funder")),
            "shared_deposit_address": bool(link.get("shared_deposit_addresses")),
            "gas_funded_by_target": any(e["src"] == target for e in gas),
            "behavioural_score": behavioural.get(addr),
            "trades_on_hl": addr in hl_active,
            "amount_correlation": corr.get("confidence"),
            "split_transfer": corr.get("split", False),
            "reentry_gap_hours": corr.get("gap_hours"),
            "age_days": round(age_days, 1) if age_days is not None else None,
        }
        confidence, reasons = score_confidence(ev)
        classification = classify_node(ev, confidence)

        nodes.append({
            "wallet": addr,
            "classification": classification,
            "confidence": confidence,
            "confidence_reasons": reasons,
            "depth": depths[addr],
            "path": path_to(addr),
            "multi_hop": depths[addr] > 1,
            "totals": {
                "received_from_target_usd": out_usd,
                "sent_to_target_usd": in_usd,
                "edge_count": len(incident),
            },
            "chains": sorted({e["chain"] for e in incident}),
            "assets": sorted({e["asset"] for e in incident if e.get("asset")}),
            "discovery_sources": sorted({e["discovery_source"] for e in incident}),
            "first_seen": _iso(min(stamps)) if stamps else None,
            "last_seen": _iso(max(stamps)) if stamps else None,
            "edge_ids": [e["id"] for e in incident],
            "evidence": ev,
        })

    nodes.sort(key=lambda n: (-CLASS_ORDER[n["classification"]], -n["confidence"]))

    all_stamps = [e["ts"] for e in wallet_edges if e["ts"]]
    reached_depth = max(depths.values()) if depths else 0
    # A frontier is incomplete when the walk stopped for a reason other than
    # running out of graph: budget exhausted, node cap hit, or depth cap reached
    # while expandable nodes remained.
    at_node_cap = len(depths) >= max_nodes
    expansion = expansion or {"status": "not_attempted"}
    sources = sorted({e["discovery_source"] for e in wallet_edges})

    return {
        "computed_at": utc_now(),
        "target": target,
        "node_count": len(nodes),
        "edge_count": len(wallet_edges),
        "service_count": len(services),
        "max_depth_reached": reached_depth,
        "services": services,
        "nodes": nodes,
        "edges": wallet_edges,
        # Graph health: enough to tell "nothing out there" from "we stopped looking".
        "health": {
            "expansion": expansion,
            "nodes_explored": len(depths),
            "edges_explored": len(wallet_edges),
            "max_depth_configured": max_depth,
            "max_depth_reached": reached_depth,
            "node_budget": max_nodes,
            "node_budget_exhausted": at_node_cap,
            "depth_limited": reached_depth >= max_depth,
            # Anything other than a clean "ok" expansion means the graph may be
            # smaller than reality. A skipped expansion is NOT the same as
            # "nothing further exists" — that ambiguity is exactly what the
            # depth-1 graph looked like before these diagnostics existed.
            "frontier_incomplete": bool(
                at_node_cap
                or reached_depth >= max_depth
                or expansion.get("status") != "ok"
            ),
            "discovery_sources": sources,
            "degraded_sources": expansion.get("degraded_sources", []),
            "oldest_evidence": _iso(min(all_stamps)) if all_stamps else None,
            "newest_evidence": _iso(max(all_stamps)) if all_stamps else None,
        },
    }


# --- alerting -------------------------------------------------------------------

def alert_key(node: dict) -> str:
    """Cooldown key that changes when the finding materially changes, so a
    strengthened conclusion re-alerts but a re-scan of the same state does not."""
    band = int(node["confidence"] * 10)
    return f"tg_{node['wallet'].lower()}_{node['classification']}_{band}"


def select_alerts(graph: dict, previous: dict | None = None,
                 min_confidence: float = 0.40) -> list[dict]:
    """Decide which discoveries are worth an email.

    Fires on: a genuinely new wallet, a new multi-hop path to a known wallet, a
    funded wallet that has started trading, a classification upgrade, or a
    material confidence increase. Everything else stays on the dashboard.
    """
    prev_nodes = {n["wallet"].lower(): n for n in (previous or {}).get("nodes", [])}
    out = []
    for node in graph.get("nodes", []):
        if node["classification"] == CLASS_SERVICE:
            continue
        prev = prev_nodes.get(node["wallet"].lower())
        reasons = []

        if prev is None:
            if node["confidence"] < min_confidence and not node["evidence"].get("direct_from_target"):
                continue
            reasons.append("newly discovered wallet" if node["depth"] == 1
                           else f"newly discovered via {node['depth']}-hop path")
        else:
            if CLASS_ORDER[node["classification"]] > CLASS_ORDER.get(
                    prev.get("classification", CLASS_DIRECT_RECIPIENT), 0):
                reasons.append(f"upgraded from {prev['classification']} "
                               f"to {node['classification']}")
            if node["confidence"] - float(prev.get("confidence", 0)) >= 0.15:
                reasons.append(f"confidence rose {prev.get('confidence')} -> {node['confidence']}")
            if node["depth"] < int(prev.get("depth", 99)):
                reasons.append(f"shorter path found ({prev.get('depth')} -> {node['depth']} hops)")
            if node["evidence"].get("trades_on_hl") and not (
                    prev.get("evidence") or {}).get("trades_on_hl"):
                reasons.append("wallet funded by the target has started trading on Hyperliquid")
            if not reasons:
                continue

        if node["confidence"] < min_confidence and node["classification"] in (
                CLASS_DIRECT_RECIPIENT,):
            # A bare recipient with no corroboration is watchlist material only.
            continue
        out.append({"node": node, "trigger_reasons": reasons})

    out.sort(key=lambda a: -a["node"]["confidence"])
    return out


def _format_path(path: list[str]) -> str:
    return "\n    -> ".join(path) if path else "(unknown)"


def fire_alerts(graph: dict, alerts: list[dict]) -> int:
    """Send one email per meaningful discovery, with the full audit trail."""
    from src.alerts import alert_transfer_graph_discovery

    edges_by_id = {e["id"]: e for e in graph.get("edges", [])}
    sent = 0
    for a in alerts:
        node = a["node"]
        edges = [edges_by_id[i] for i in node["edge_ids"] if i in edges_by_id]
        edges.sort(key=lambda e: e["ts"] or 0)
        if alert_transfer_graph_discovery(node, a["trigger_reasons"], edges):
            sent += 1
    return sent


# --- I/O wrapper ----------------------------------------------------------------

def _load_behavioural_scores() -> tuple[dict, set]:
    """Behavioural similarity per wallet, and which wallets are actively trading."""
    scores: dict[str, float] = {}
    active: set = set()
    path = DATA_DIR / "candidates" / "latest.json"
    if path.exists():
        try:
            with open(path) as f:
                for c in json.load(f).get("candidates", []):
                    w = (c.get("wallet") or "").lower()
                    if not w:
                        continue
                    scores[w] = float(c.get("best_score", 0) or 0)
                    if c.get("status") == "ACTIVE":
                        active.add(w)
        except (OSError, ValueError) as e:
            print(f"[graph] could not read candidates: {e}")
    return scores, active


def _load_linkage_evidence() -> dict:
    """Shared-funder / address-reuse evidence already gathered by the scanner."""
    out: dict[str, dict] = {}
    path = DATA_DIR / "candidates" / "latest.json"
    if path.exists():
        try:
            with open(path) as f:
                for c in json.load(f).get("candidates", []):
                    link = (c.get("latest_evidence") or {}).get("linkage")
                    if link:
                        out[(c.get("wallet") or "").lower()] = link
        except (OSError, ValueError):
            pass
    return out


def _load_correlations() -> dict:
    """Deposit/withdrawal amount correlations from correlator.py."""
    out: dict[str, dict] = {}
    path = DATA_DIR / "correlations" / "latest.json"
    if path.exists():
        try:
            with open(path) as f:
                for m in json.load(f).get("matches", []):
                    w = (m.get("wallet") or "").lower()
                    if w:
                        out[w] = {"confidence": m.get("confidence"),
                                  "gap_hours": m.get("gap_hours"),
                                  "split": False}
        except (OSError, ValueError):
            pass
    return out


def collect_known_edges() -> list[dict]:
    """Build edges from data already on disk — no API calls, no budget needed."""
    edges = []

    for tx in load_all_records(str(DATA_DIR / "l1_transactions")):
        e = normalise_l1_transfer(tx)
        if e:
            edges.append(e)

    for entry in load_all_records(str(DATA_DIR / "ledger")):
        e = normalise_hl_ledger_entry(entry)
        if e:
            edges.append(e)

    # Fund-flow findings carry hops the raw tables don't (2- and 3-hop traces).
    ff = DATA_DIR / "fund_flows" / "latest.json"
    if ff.exists():
        try:
            with open(ff) as f:
                for finding in json.load(f).get("findings", []):
                    src = (finding.get("source") or "").lower()
                    dst = (finding.get("destination") or "").lower()
                    if not src or not dst or src == dst:
                        continue
                    ts = 0
                    try:
                        ts = int(datetime.fromisoformat(
                            finding["detected_at"]).timestamp())
                    except (KeyError, TypeError, ValueError):
                        pass
                    edges.append({
                        "id": edge_id(src, dst, CHAIN_ARBITRUM,
                                      finding.get("tx_hash", ""), ts),
                        "src": src, "dst": dst, "chain": CHAIN_ARBITRUM,
                        "asset": "USDC",
                        "amount_usd": round(float(finding.get("amount_usdc_raw", 0) or 0), 2),
                        "ref": finding.get("tx_hash", ""),
                        "ts": ts, "timestamp": _iso(ts),
                        "discovery_source": SRC_L1,
                        "hop_count": finding.get("hop_count", 1),
                    })
        except (OSError, ValueError) as e:
            print(f"[graph] could not read fund_flows: {e}")

    return edges


def expand_frontier(edges: list[dict], target: str,
                    budget: dict) -> tuple[list[dict], dict]:
    """Widen the graph with Etherscan lookups, under a hard budget.

    Returns (edges, diagnostics). The diagnostics say explicitly whether expansion
    ran, was skipped, degraded or failed — without them a depth-1 graph is
    ambiguous between "no deeper links exist" and "we never looked". In CI the
    secret is present so this runs automatically; locally it degrades cleanly.

    Only unexplored non-service wallets that received funds from the target are
    expanded, newest-first, and only while the call and wall-clock budgets hold.
    """
    import os

    diag = {
        "status": "not_attempted",
        "attempted_at": utc_now(),
        "lookups": 0,
        "lookup_budget": budget["max_expansions"],
        "time_budget_seconds": budget["time_budget_seconds"],
        "new_edges": 0,
        "wallets_expanded": [],
        "frontier_size": 0,
        "frontier_remaining": 0,
        "degraded_sources": [],
        "error": None,
    }

    if not os.environ.get("ETHERSCAN_API_KEY"):
        diag["status"] = "skipped_no_api_key"
        diag["degraded_sources"] = ["arbitrum_l1"]
        print("[graph] ETHERSCAN_API_KEY absent — L1 frontier expansion SKIPPED. "
              "Graph is limited to locally recorded edges (typically depth 1).")
        return edges, diag

    from src.tracer import get_usdc_transfers

    deadline = time.monotonic() + budget["time_budget_seconds"]
    max_calls = budget["max_expansions"]
    target = target.lower()

    services = detect_services(edges, set())
    explored = set()

    # Expand wallets funded by the target first — those are the migration paths.
    frontier = [e["dst"] for e in sorted(
        (e for e in edges if e["src"] == target and not e.get("bridge_event")),
        key=lambda e: -(e["ts"] or 0))]
    frontier = [w for w in dict.fromkeys(frontier)
                if w not in services and w != target]
    diag["frontier_size"] = len(frontier)

    calls = 0
    added = []
    known_ids = {x["id"] for x in edges}
    budget_hit = False
    try:
        for wallet in frontier:
            if calls >= max_calls or time.monotonic() > deadline:
                budget_hit = True
                print(f"[graph] expansion budget reached after {calls} lookup(s); stopping")
                break
            if wallet in explored:
                continue
            explored.add(wallet)
            calls += 1
            for tx in get_usdc_transfers(wallet):
                e = normalise_l1_transfer(tx)
                if e and e["id"] not in known_ids:
                    known_ids.add(e["id"])
                    added.append(e)
    except Exception as exc:  # network/API failure must not lose partial results
        diag["status"] = "failed"
        diag["error"] = str(exc)[:200]
        diag["degraded_sources"] = ["arbitrum_l1"]
        print(f"[graph] frontier expansion FAILED after {calls} lookup(s): {exc}")
        diag["lookups"] = calls
        diag["new_edges"] = len(added)
        diag["wallets_expanded"] = sorted(explored)
        diag["frontier_remaining"] = max(0, len(frontier) - len(explored))
        return edges + added, diag

    diag["lookups"] = calls
    diag["new_edges"] = len(added)
    diag["wallets_expanded"] = sorted(explored)
    diag["frontier_remaining"] = max(0, len(frontier) - len(explored))
    diag["status"] = "budget_exhausted" if budget_hit else "ok"
    diag["completed_at"] = utc_now()

    print(f"[graph] L1 frontier expansion {diag['status']}: {calls} lookup(s), "
          f"{len(added)} new edge(s), {diag['frontier_remaining']} wallet(s) unexplored")
    write_cursor("transfer_graph_last_expansion_ms", now_ms())
    return edges + added, diag


def run_transfer_graph(expand: bool = True) -> dict:
    """Full pipeline: gather edges, traverse, score, persist, alert."""
    config = load_config()
    target = config["target_wallet"].lower()
    cfg = {**DEFAULTS, **(config.get("transfer_graph") or {})}

    known_services = {a.lower() for a in config.get("excluded_addresses", [])}
    known_services.add(config["hl_bridge_contract"].lower())
    known_services.add(config["usdc_contract_arbitrum"].lower())
    known_services |= {a.lower() for a in config.get("known_service_addresses", [])}

    edges = collect_known_edges()
    print(f"[graph] {len(edges)} edge(s) from local data")
    if expand:
        edges, expansion = expand_frontier(edges, target, cfg)
    else:
        expansion = {"status": "disabled", "degraded_sources": ["arbitrum_l1"],
                     "attempted_at": utc_now()}
    # Carry the previous successful expansion forward so the dashboard can show
    # "last successful L1 expansion" even on a run where it was skipped.
    prev_path = DATA_DIR / "transfer_graph" / "latest.json"
    if expansion.get("status") != "ok" and prev_path.exists():
        try:
            with open(prev_path) as f:
                prev_health = (json.load(f).get("health") or {}).get("expansion") or {}
            if prev_health.get("status") == "ok":
                expansion["last_successful"] = prev_health.get("completed_at")
            elif prev_health.get("last_successful"):
                expansion["last_successful"] = prev_health["last_successful"]
        except (OSError, ValueError):
            pass
    elif expansion.get("status") == "ok":
        expansion["last_successful"] = expansion.get("completed_at")

    behavioural, hl_active = _load_behavioural_scores()
    graph = build_graph(
        edges, target,
        known_services=known_services,
        behavioural=behavioural,
        hl_active=hl_active,
        linkage=_load_linkage_evidence(),
        correlations=_load_correlations(),
        max_depth=cfg["max_depth"],
        max_nodes=cfg["max_nodes"],
        dust_usd=cfg["dust_usd"],
        expansion=expansion,
    )

    previous = None
    prev_path = DATA_DIR / "transfer_graph" / "latest.json"
    if prev_path.exists():
        try:
            with open(prev_path) as f:
                previous = json.load(f)
        except (OSError, ValueError):
            previous = None

    alerts = select_alerts(graph, previous)
    graph["alerts_fired"] = fire_alerts(graph, alerts) if alerts else 0
    graph["pending_alerts"] = len(alerts)

    save_latest(str(DATA_DIR / "transfer_graph"), graph)

    by_class: dict[str, int] = {}
    for n in graph["nodes"]:
        by_class[n["classification"]] = by_class.get(n["classification"], 0) + 1
    print(f"[graph] {graph['node_count']} node(s), {graph['edge_count']} edge(s), "
          f"{graph['service_count']} service(s), depth {graph['max_depth_reached']}")
    for cls in sorted(by_class, key=lambda c: -CLASS_ORDER[c]):
        print(f"[graph]   {cls}: {by_class[cls]}")
    if alerts:
        print(f"[graph] {graph['alerts_fired']}/{len(alerts)} discovery alert(s) sent")
    return graph


def main():
    run_transfer_graph()


if __name__ == "__main__":
    main()
