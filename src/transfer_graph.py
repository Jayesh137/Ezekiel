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

from src import continuity as ct
from src import thresholds as th
from src.utils import (
    DATA_DIR,
    candidate_current_score,
    load_all_records,
    load_config,
    now_ms,
    save_latest,
    write_cursor,
)


def _resolved_thresholds() -> dict:
    """The thresholds in force, resolved the same way the scanner resolves them.

    Looked up lazily so the pure scoring functions below stay callable with just
    their evidence, while build_graph resolves once and passes the same set to
    every node rather than re-reading per wallet.
    """
    report = th.load_backtest_report(DATA_DIR.parent / "profile")
    return th.resolve(load_config()["alert_thresholds"], report)

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
    "max_depth": 4,
    "max_nodes": 300,
    "max_branching": 8,
    "max_expansions": 40,     # Etherscan-backed hop expansions per run
    "time_budget_seconds": 150,
    "min_edge_usd": 1000.0,
    "dust_usd": 1.0,
    # Fan degree at/above which an address is treated as a service, not a wallet.
    "service_fanout": 25,
    "service_fanin": 25,
}

# Hard ceilings on the state the graph carries between runs, so a hub address
# cannot grow latest.json without bound. Truncation is always reported.
#
# Sizing evidence (live graph, 2026-07-29: 8.1 MB file, 17,405 edges):
#   frontier entry ~70 B -> 2000 entries = 137 KB = 1.7% of the file, against
#   6.2 MB of edges (77%). The previous cap of 200 saved 0.17% while discarding
#   1,168 of 1,368 pending wallets, so it was costing chase coverage to protect
#   nothing. Ranking the full frontier costs 0.019 s with EdgeIndex (was 4.4 s).
MAX_FRONTIER_QUEUE = 2000
MAX_EXPANDED_LEDGER = 2000
# `decisions` is unbounded in practice — 1,409 entries / 225 KB on the live
# graph, already larger than a full frontier queue — and grows with the frontier.
MAX_DECISIONS = 3000

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


def score_confidence(ev: dict, thresholds: dict | None = None) -> tuple[float, list[str]]:
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

    t = thresholds if thresholds is not None else _resolved_thresholds()
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
        # Gate and scale on the thresholds in force, not a literal 0.65. The band
        # keeps its original shape — a wallet at the gate contributes 40% of this
        # weight, rising to all of it — but the top now anchors on the proven
        # self-match ceiling rather than an unreachable 1.0.
        if b >= th.behavioural_gate(t):
            strength = th.behavioural_strength(b, t)
            corroboration += 0.25 * (0.4 + 0.6 * strength)
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


def classify_node(ev: dict, confidence: float, thresholds: dict | None = None) -> str:
    """Grade a wallet from its evidence. Deliberately conservative.

    MIGRATION_CANDIDATE requires an independent corroborating vector, so a large
    transfer alone can never reach it.
    """
    if ev.get("is_service"):
        return CLASS_SERVICE

    t = thresholds if thresholds is not None else _resolved_thresholds()
    corroborated = bool(
        (ev.get("behavioural_score") or 0) >= th.behavioural_gate(t)
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

SCHEMA_VERSION = 2


def migrate_graph(graph: dict | None) -> dict:
    """Read any historical transfer_graph document as schema v2.

    The live production file is v1: no `schema_version`, no `chains`, and no
    per-node `lifecycle`/`continuity`. Migration is ADDITIVE and idempotent —
    every historical node, edge, service and evidence reference is preserved
    untouched; only absent v2 containers are filled in with empty defaults.
    Nothing is recomputed here, because a stored v1 document has no hop
    evidence to score and inventing one would fabricate history.
    """
    if not isinstance(graph, dict):
        return {}
    out = dict(graph)
    version = int(out.get("schema_version") or 1)
    out["schema_version"] = SCHEMA_VERSION
    if version < 2:
        out["migrated_from_schema"] = version

    out.setdefault("chains", [])
    out.setdefault("chain_count", len(out.get("chains") or []))
    out.setdefault("undelivered_alerts", [])
    out.setdefault("alerted_paths", [])
    out.setdefault("alerted_lifecycle", {})

    health = dict(out.get("health") or {})
    expansion = dict(health.get("expansion") or {})
    expansion.setdefault("status", "not_attempted")
    expansion.setdefault("frontier_queue", [])
    expansion.setdefault("expanded_ledger", [])
    expansion.setdefault("frontier_truncated", 0)
    expansion.setdefault("frontier_eligible", len(expansion.get("frontier_queue") or []))
    expansion.setdefault("frontier_retained", len(expansion.get("frontier_queue") or []))
    expansion.setdefault("frontier_cap", 0)
    expansion.setdefault("decisions_truncated", 0)
    expansion.setdefault("partial_failures", [])
    health["expansion"] = expansion
    out["health"] = health

    nodes = []
    for n in out.get("nodes") or []:
        m = dict(n)
        m.setdefault("continuity", None)
        m.setdefault("lifecycle", None)
        m.setdefault("signals", [])
        m.setdefault("chain_id", None)
        m.setdefault("value_retained", None)
        m.setdefault("traced_value_usd", None)
        m.setdefault("path_truncated", False)
        nodes.append(m)
    out["nodes"] = nodes
    return out


def build_chains(target: str, nodes: list, edges: list, parents: dict,
                 services: dict, now_ts: float) -> list:
    """Assemble ordered, evidence-carrying paths from the target to each wallet.

    The previous model kept only `path: [addr, addr]` — a bare address list with
    no per-hop amount, timing or reference, so a conclusion could not be audited
    and value flow could not be measured. Each hop here retains its full evidence.
    """
    by_pair = {}
    for e in edges:
        if e.get("bridge_event"):
            continue
        key = (e["src"], e["dst"])
        prev = by_pair.get(key)
        # Keep the largest transfer as the representative hop for the pair.
        if prev is None or float(e.get("amount_usd", 0) or 0) > float(
                prev.get("amount_usd", 0) or 0):
            by_pair[key] = e

    relay_cache: dict[str, dict] = {}

    def is_relay(addr: str) -> bool:
        if addr not in relay_cache:
            relay_cache[addr] = _relay_profile(addr, edges)
        return relay_cache[addr]["relay"]["is_relay"]

    chains = []
    for node in nodes:
        addr = node["wallet"]
        walk = node.get("path") or []
        if len(walk) < 2:
            continue
        hops = []
        breaks = []
        relay_hops = []
        for a, b in zip(walk, walk[1:], strict=False):
            e = by_pair.get((a, b))
            if e is None:
                # A hop with no recorded directed transfer TRUNCATES the chain.
                # Skipping it and carrying on spliced two unconnected hops into
                # one "ordered path" and measured value_retained between them —
                # a wallet reached only by a reverse edge could therefore show a
                # contiguous path retaining 100% of value it never received.
                breaks.append({
                    "at": a,
                    "reason": f"no recorded transfer {a[:10]}… -> {b[:10]}…",
                    "type": ct.BREAK_INCOMPLETE})
                break
            hops.append({
                "src": e["src"], "dst": e["dst"], "chain": e["chain"],
                "asset": e.get("asset"), "amount_usd": e.get("amount_usd"),
                "ts": e.get("ts"), "timestamp": e.get("timestamp"),
                "ref": e.get("ref"), "discovery_source": e.get("discovery_source"),
            })
            if b in services:
                breaks.append({"at": b, "reason": services[b], "type": ct.BREAK_SERVICE})
                break  # value entering a service leaves observable custody
            if is_relay(b):
                relay_hops.append(b)
        if not hops:
            continue
        chain = ct.build_path(hops, breaks=breaks, relay_hops=relay_hops)
        # The endpoint is wherever the traced value actually got to. Forcing it
        # to the node address claimed reach the hops do not support.
        chain["endpoint"] = hops[-1]["dst"]
        chain["requested_endpoint"] = addr
        chain["complete"] = (chain["endpoint"] == addr and not breaks)
        chains.append(chain)
    return chains


def _continuity_for(node: dict, chain: dict | None, evidence: dict,
                    disposition_alert: bool = False,
                    thresholds: dict | None = None) -> dict:
    """Translate node evidence + its best chain into continuity signals, a score
    and a lifecycle state. Kept thin: all judgement lives in src/continuity.py."""
    hops = chain["hop_count"] if chain else 1
    retained = chain["value_retained"] if chain else 1.0
    breaks = chain["breaks"] if chain else []

    signals = {}
    if evidence.get("direct_from_target"):
        signals["direct_from_target"] = True
    if evidence.get("gas_funded_by_target"):
        signals["first_gas"] = True
    if evidence.get("transfer_count", 0) >= 3:
        signals["repeated_transfers"] = True
    if evidence.get("bidirectional"):
        signals["two_way_flow"] = True
    if evidence.get("hl_native"):
        signals["hl_native"] = True
    if evidence.get("shared_deposit_address") or evidence.get("shared_funder"):
        signals["shared_route"] = True
    if evidence.get("amount_correlation"):
        signals["amount_similarity"] = float(evidence["amount_correlation"])
    gap = evidence.get("reentry_gap_hours")
    if gap is not None:
        signals["temporal_proximity"] = ct.temporal_proximity(gap)
    b = evidence.get("behavioural_score")
    if b is not None and float(b) >= th.behavioural_gate(
            thresholds if thresholds is not None else _resolved_thresholds()):
        signals["behavioural"] = float(b)
    # "Funded by the target" must mean target money REACHED this wallet, whether
    # directly or along an unbroken chain — tying it to a direct edge made every
    # multi-hop endpoint permanently ineligible, which defeats the whole point of
    # following funds through intermediaries.
    reached_by_target_funds = bool(
        evidence.get("direct_from_target")
        or (chain and not breaks and retained >= 0.5)
    )
    if evidence.get("trades_on_hl") and reached_by_target_funds:
        signals["funded_before_trading"] = True
    if chain and chain.get("value_retained", 0) >= 0.7 and hops > 1:
        signals["value_retained"] = chain["value_retained"]
    if evidence.get("split_merge"):
        signals["split_merge"] = float(evidence["split_merge"])
    if evidence.get("bridge_correlated"):
        signals["bridge_correlated"] = float(evidence["bridge_correlated"])
    if evidence.get("rotation"):
        signals["rotation"] = float(evidence["rotation"])

    scored = ct.score_continuity(
        signals,
        hop_count=hops,
        value_retained=retained,
        age_days=evidence.get("age_days"),
        is_service=evidence.get("is_service", False),
        vetoes=evidence.get("vetoes"),
        breaks=breaks,
    )
    life = ct.lifecycle_state(
        is_service=evidence.get("is_service", False),
        on_path=bool(chain),
        transfer_count=int(evidence.get("transfer_count", 0) or 0),
        runs_seen=int(evidence.get("runs_seen", 1) or 1),
        funded_by_target=reached_by_target_funds,
        has_unbroken_path=bool(chain) and not breaks,
        trades_after_funding=bool(evidence.get("trades_on_hl")),
        confidence=scored["confidence"],
        families=scored["families"],
        vetoes=evidence.get("vetoes"),
        breaks=breaks,
        days_inactive=evidence.get("age_days"),
        disposition_alert=disposition_alert,
    )
    return {"signals": signals, "continuity": scored, "lifecycle": life}


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
                expansion: dict | None = None,
                thresholds: dict | None = None) -> dict:
    """Walk outward from the target, classifying and scoring every wallet reached.

    Pure function — every external input is passed in, so traversal and scoring
    are fully testable offline. `thresholds` is resolved once here and handed to
    every node rather than re-read per wallet.
    """
    target = target.lower()
    thresholds = thresholds if thresholds is not None else _resolved_thresholds()
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
        confidence, reasons = score_confidence(ev, thresholds)
        classification = classify_node(ev, confidence, thresholds)

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

    # Ordered evidence-carrying paths, then continuity scoring and lifecycle.
    chains = build_chains(target, nodes, wallet_edges, parents, services, now_ts)
    # Scoring is keyed on where value ACTUALLY arrived, never on where a walk
    # was headed. A truncated chain scores its real endpoint and leaves the
    # unreached wallet with no chain — which is the honest outcome.
    chain_by_endpoint = {}
    truncated_for = {}
    for ch in chains:
        if not ch.get("complete"):
            truncated_for.setdefault(ch["requested_endpoint"], ch)
        prev = chain_by_endpoint.get(ch["endpoint"])
        if prev is None or ch["value_retained"] > prev["value_retained"]:
            chain_by_endpoint[ch["endpoint"]] = ch
    for n in nodes:
        ch = chain_by_endpoint.get(n["wallet"])
        if ch is not None and ch["endpoint"] != n["wallet"]:
            ch = None
        cont = _continuity_for(n, ch, n["evidence"], thresholds=thresholds)
        n["continuity"] = cont["continuity"]
        n["lifecycle"] = cont["lifecycle"]
        n["signals"] = sorted(cont["signals"])
        n["chain_id"] = ch["id"] if ch else None
        n["value_retained"] = ch["value_retained"] if ch else None
        n["traced_value_usd"] = ch["value_end_usd"] if ch else None
        trunc = truncated_for.get(n["wallet"])
        n["path_truncated"] = bool(trunc) and ch is None
        n["path_truncated_at"] = (
            (trunc["breaks"][0].get("at") if trunc.get("breaks") else None)
            if n["path_truncated"] else None)

    nodes.sort(key=lambda n: (-CLASS_ORDER[n["classification"]], -n["confidence"]))

    all_stamps = [e["ts"] for e in wallet_edges if e["ts"]]
    reached_depth = max(depths.values()) if depths else 0
    # A frontier is incomplete when the walk stopped for a reason other than
    # running out of graph: budget exhausted, node cap hit, or depth cap reached
    # while expandable nodes remained.
    at_node_cap = len(depths) >= max_nodes
    # Always emit the full expansion shape. A caller that ran no expansion still
    # has to produce the fields the dashboard and the next run's resume logic
    # read, or they silently become "undefined" rather than "zero".
    expansion = {
        "status": "not_attempted", "lookups": 0, "lookup_budget": 0,
        "new_edges": 0, "wallets_expanded": [], "frontier_size": 0,
        "frontier_remaining": 0, "frontier_queue": [], "frontier_truncated": 0,
        "frontier_eligible": 0, "frontier_retained": 0, "frontier_cap": 0,
        "decisions_truncated": 0,
        "expanded_ledger": [], "skipped_already_expanded": 0,
        "partial_failures": [], "decisions": [], "deepest_expanded": 0,
        "degraded_sources": [], "error": None,
        **(expansion or {}),
    }
    sources = sorted({e["discovery_source"] for e in wallet_edges})

    return {
        "schema_version": SCHEMA_VERSION,
        "computed_at": utc_now(),
        "target": target,
        "chains": chains,
        "chain_count": len(chains),
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
    funded wallet that has started trading, a lifecycle promotion, a
    classification upgrade, or a material confidence increase. Everything else
    stays on the dashboard.

    Every trigger is a TRANSITION against the previous saved graph, so rebuilding
    an unchanged graph sends nothing.
    """
    prev_nodes = {n["wallet"].lower(): n for n in (previous or {}).get("nodes", [])}
    # Discoveries selected on a previous run but never delivered (SMTP down, auth
    # rejected). They must be re-selected even though the node itself is unchanged,
    # otherwise advancing the saved graph silently retires an undelivered alert.
    undelivered = {w.lower() for w in (previous or {}).get("undelivered_alerts", [])}
    # Route signatures already alerted on. A resumed frontier rediscovers the
    # same route with fresh transaction ids, which changes the path id but not
    # the route — without this the same chain re-alerts on every resume.
    alerted_paths = set((previous or {}).get("alerted_paths") or [])
    prev_lifecycle = dict((previous or {}).get("alerted_lifecycle") or {})
    chains_by_id = {c["id"]: c for c in (graph.get("chains") or [])}

    out = []
    for node in graph.get("nodes", []):
        if node["classification"] == CLASS_SERVICE:
            continue
        wallet = node["wallet"].lower()
        life = (node.get("lifecycle") or {}).get("state")
        # A service can never alert, whichever vocabulary named it one.
        if life == ct.LIFECYCLE_REJECTED_SERVICE:
            continue
        prev = prev_nodes.get(wallet)
        reasons = []

        # Resolved before the retry branch: a retry that finally lands must mark
        # its route delivered too, or the same multi-hop chain alerts again on
        # the following run.
        chain = chains_by_id.get(node.get("chain_id"))
        signature = chain.get("signature") if chain else None
        new_route = bool(signature) and signature not in alerted_paths

        if wallet in undelivered:
            out.append({"node": node, "path_signature": signature,
                        "trigger_reasons": ["retry: previously selected but not delivered"]})
            continue

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
            # Lifecycle promotion is the continuity tracker's material event.
            # Without this a wallet could walk all the way to
            # HIGH_CONFIDENCE_SUCCESSOR without ever sending an email.
            #
            # The baseline is the delivered high-water mark when we have one, and
            # otherwise the state the previous graph recorded for this node.
            # Reading only `alerted_lifecycle` made every wallet look newly
            # promoted on any graph that predates that field — including every
            # schema-v1 file in production — which would have emailed the whole
            # graph on the first run after deploy.
            was = prev_lifecycle.get(wallet) or (prev.get("lifecycle") or {}).get("state")
            if (life and ct.LIFECYCLE_ORDER.get(life, 0)
                    > ct.LIFECYCLE_ORDER.get(was, -99)):
                reasons.append(f"continuity lifecycle {was or 'new'} -> {life}")
            if new_route and node["depth"] > 1:
                reasons.append(
                    f"new {chain['hop_count']}-hop route retaining "
                    f"{chain['value_retained']:.0%} of traced value")
            if not reasons:
                continue

        if node["confidence"] < min_confidence and node["classification"] in (
                CLASS_DIRECT_RECIPIENT,):
            # A bare recipient with no corroboration is watchlist material only.
            continue
        out.append({"node": node, "trigger_reasons": reasons,
                    "path_signature": signature})

    out.sort(key=lambda a: -a["node"]["confidence"])
    return out


def annotate_changes(graph: dict, previous: dict | None) -> None:
    """Record what moved since the last run, in place on `graph`.

    build_graph is pure and sees only the current edges, so "confidence rose 12
    points" cannot be computed there. Without it the dashboard can show a
    number but not whether that number is climbing — which is the part that
    matters when watching for a migration in progress.
    """
    prev_nodes = {n["wallet"].lower(): n for n in (previous or {}).get("nodes") or []}
    for n in graph.get("nodes") or []:
        prev = prev_nodes.get(n["wallet"].lower())
        cont = (n.get("continuity") or {}).get("confidence")
        if prev is None:
            n["is_new"] = True
            n["confidence_delta"] = None
            n["continuity_delta"] = None
            n["previous_lifecycle"] = None
            continue
        prev_cont = (prev.get("continuity") or {}).get("confidence")
        n["is_new"] = False
        n["confidence_delta"] = round(
            float(n["confidence"]) - float(prev.get("confidence") or 0.0), 4)
        n["continuity_delta"] = (
            round(float(cont) - float(prev_cont), 4)
            if cont is not None and prev_cont is not None else None)
        n["previous_lifecycle"] = (prev.get("lifecycle") or {}).get("state")


def advance_alert_state(graph: dict, previous: dict | None,
                        alerts: list[dict], undelivered: list[str]) -> None:
    """Record what was actually DELIVERED, in place on `graph`.

    Delivery state advances only for alerts that were sent. A wallet whose email
    failed keeps its previous lifecycle/route marks, so the retry on the next run
    still reads as a transition rather than as already-reported.
    """
    failed = {w.lower() for w in undelivered}
    graph["alerted_paths"] = sorted(set((previous or {}).get("alerted_paths") or []))
    lifecycle_state = dict((previous or {}).get("alerted_lifecycle") or {})

    delivered_paths = set(graph["alerted_paths"])
    for a in alerts:
        wallet = a["node"]["wallet"].lower()
        if wallet in failed:
            continue
        if a.get("path_signature"):
            delivered_paths.add(a["path_signature"])
        state = (a["node"].get("lifecycle") or {}).get("state")
        if state:
            lifecycle_state[wallet] = state
    graph["alerted_paths"] = sorted(delivered_paths)
    graph["alerted_lifecycle"] = lifecycle_state


def _format_path(path: list[str]) -> str:
    return "\n    -> ".join(path) if path else "(unknown)"


def fire_alerts(graph: dict, alerts: list[dict]) -> tuple[int, list[str]]:
    """Send one email per meaningful discovery, with the full audit trail.

    Returns (sent_count, undelivered_wallets). The caller MUST persist the
    undelivered list: the graph state advances on every run, so a discovery that
    was selected but not delivered would otherwise look "already reported" on the
    next run and be lost permanently.
    """
    from src.alerts import alert_transfer_graph_discovery

    edges_by_id = {e["id"]: e for e in graph.get("edges", [])}
    sent = 0
    undelivered = []
    for a in alerts:
        node = a["node"]
        edges = [edges_by_id[i] for i in node["edge_ids"] if i in edges_by_id]
        edges.sort(key=lambda e: e["ts"] or 0)
        if alert_transfer_graph_discovery(node, a["trigger_reasons"], edges):
            sent += 1
        else:
            undelivered.append(node["wallet"].lower())
            print(f"[graph] alert NOT delivered for {node['wallet'][:12]}... "
                  f"({node['classification']}) — queued for retry next run")
    return sent, undelivered


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
                    # Current score, not the all-time high-water mark: the graph
                    # asserts "Trades like the target (behavioural similarity
                    # X%)" inside a CRITICAL email, so X has to be true now.
                    scores[w] = candidate_current_score(c)
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


def _positive_int(value: object, fallback: int) -> int:
    """A configured ceiling, or the default when absent/invalid.

    Backward compatibility: configs written before these keys existed simply
    omit them. A zero or negative value would mean "keep nothing", which is
    never the intent, so it falls back too.
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return n if n > 0 else fallback


class EdgeIndex:
    """Inbound/outbound edges bucketed by wallet, built once.

    _relay_profile used to filter the whole edge list per wallet, so ranking the
    frontier was O(wallets x edges): on the live graph (17,405 edges) that is
    3.2 ms per wallet, or 4.4 s to rank a 1,368-wallet frontier — which is why
    only a truncated slice was ever ranked. Indexing costs 7.7 ms once and drops
    the per-wallet cost to 0.008 ms, making it affordable to rank the ENTIRE
    frontier before deciding what to keep.
    """

    __slots__ = ("inbound", "outbound")

    def __init__(self, edges: list[dict]):
        self.inbound: dict[str, list[dict]] = {}
        self.outbound: dict[str, list[dict]] = {}
        for e in edges:
            if e.get("bridge_event"):
                continue
            self.inbound.setdefault(e["dst"], []).append(e)
            self.outbound.setdefault(e["src"], []).append(e)

    def of(self, wallet: str) -> tuple[list[dict], list[dict]]:
        return self.inbound.get(wallet, []), self.outbound.get(wallet, [])


def _relay_profile(wallet: str, edges: "list[dict] | EdgeIndex") -> dict:
    """Measure pass-through behaviour for one wallet from the edges we hold.

    Accepts a prebuilt EdgeIndex or a raw edge list, so existing callers and
    tests keep working unchanged.
    """
    if isinstance(edges, EdgeIndex):
        inbound, outbound = edges.of(wallet)
    else:
        inbound = [e for e in edges if e["dst"] == wallet and not e.get("bridge_event")]
        outbound = [e for e in edges if e["src"] == wallet and not e.get("bridge_event")]
    recv = sum(float(e.get("amount_usd", 0) or 0) for e in inbound)
    fwd = sum(float(e.get("amount_usd", 0) or 0) for e in outbound)
    dests = {e["dst"] for e in outbound}
    in_ts = [e["ts"] for e in inbound if e.get("ts")]
    out_ts = [e["ts"] for e in outbound if e.get("ts")]
    hours = max(0.0, (min(out_ts) - min(in_ts)) / 3600.0) if (in_ts and out_ts) else None
    return {
        "received_usd": recv,
        "forwarded_usd": fwd,
        "destination_count": len(dests),
        "hours_to_forward": hours,
        "relay": ct.classify_relay(recv, fwd, hours, len(dests)),
        "likelihood": ct.relay_likelihood(recv, fwd, hours, len(dests)),
        "last_seen_ts": max(out_ts + in_ts) if (out_ts or in_ts) else 0,
    }


def _frontier_rank_key(wallet: str, depth: int, priority: float,
                       prof: dict) -> tuple:
    """Total ordering for frontier retention, strongest first.

    The wallet address is the LAST field and breaks nothing but an exact tie on
    every meaningful signal. Sorting by (depth, wallet) — as truncation used to —
    discarded chase targets alphabetically: on the live graph that threw away
    1,168 of 1,368 pending wallets on address order alone.
    """
    return (
        -round(float(priority), 6),                    # composite chase priority
        depth,                                         # shorter path first
        -round(float(prof.get("received_usd", 0.0)), 2),   # traced value
        -round(float(prof.get("likelihood", 0.0)), 6),     # relay likelihood
        -int(prof.get("last_seen_ts", 0) or 0),        # recency
        wallet,                                        # deterministic tie-break only
    )


def _frontier_priority(wallet: str, depth: int, edges: "list[dict] | EdgeIndex",
                       now_ts: float) -> tuple:
    """Rank a candidate by traced value x relay likelihood x recency.

    Budget is finite, so it must go where a chain is most likely to continue. A
    relay ranks high here even though its OWNERSHIP confidence is low - holding
    nothing is exactly what a relay does, which is why confidence is the wrong
    signal for deciding what to chase.
    """
    prof = _relay_profile(wallet, edges)
    value = prof["received_usd"]
    value_score = min(1.0, value / 1_000_000.0) if value > 0 else 0.0
    age_days = ((now_ts - prof["last_seen_ts"]) / 86400.0
                if prof["last_seen_ts"] else 999.0)
    recency = ct.age_decay(age_days)
    depth_penalty = 0.85 ** max(0, depth - 1)
    score = (0.45 * value_score + 0.35 * prof["likelihood"]
             + 0.20 * recency) * depth_penalty
    return round(score, 4), prof


def _expandable_edges(edges: list[dict], dust_usd: float) -> list[dict]:
    """The edges the frontier is allowed to walk.

    Must match build_graph's filter. When it did not, the frontier seeded itself
    from raw edges while build_graph discarded sub-dust ones, so lookups were
    spent on address-poisoning clones that never entered the graph: on the live
    2026-07-28 run, 6 of 11 lookups went to six such addresses (770 of the
    target's 874 recorded out-edges are sub-dollar poisoning transfers) and one
    of them contributed zero edges to the finished graph.
    """
    keep = []
    for e in edges:
        if e.get("bridge_event"):
            continue
        if (float(e.get("amount_usd", 0) or 0) < dust_usd
                and e.get("discovery_source") != SRC_GAS_FUNDING):
            continue
        keep.append(e)
    return keep


def expand_frontier(edges: list[dict], target: str, budget: dict,
                    resume: list | None = None,
                    now_ts: float | None = None,
                    known_services: set | None = None,
                    already_expanded: list | None = None) -> tuple[list[dict], dict]:
    """Iteratively widen the graph, level by level, under a hard budget.

    Replaces a single round that only looked up the target's DIRECT recipients:
    that revealed hop-2 edges but never expanded hop-2 wallets, so hop-3 and
    beyond were structurally unreachable regardless of max_depth. The live graph
    sat at depth 2 of 3 with 11 of 40 lookups unused.

    Unfinished frontier is returned in `frontier_queue` so the next run resumes
    rather than restarting the traversal.

    Returns (edges, diagnostics). Diagnostics record, per wallet, whether it was
    expanded / deferred / suppressed and why.
    """
    import os

    now_ts = now_ts if now_ts is not None else time.time()
    diag = {
        "status": "not_attempted",
        "attempted_at": utc_now(),
        "lookups": 0,
        "lookup_budget": budget["max_expansions"],
        "time_budget_seconds": budget["time_budget_seconds"],
        "max_depth": budget.get("max_depth", DEFAULTS["max_depth"]),
        "new_edges": 0,
        "wallets_expanded": [],
        "frontier_size": 0,
        "frontier_remaining": 0,
        "frontier_queue": [],
        "frontier_truncated": 0,
        "frontier_eligible": 0,
        "frontier_retained": 0,
        "frontier_cap": 0,
        "decisions_truncated": 0,
        "expanded_ledger": [],
        "skipped_already_expanded": 0,
        "decisions": [],
        "deepest_expanded": 0,
        "degraded_sources": [],
        "partial_failures": [],
        "error": None,
    }

    def decide(wallet, depth, action, reason, priority=None):
        diag["decisions"].append({"wallet": wallet, "depth": depth,
                                  "action": action, "reason": reason,
                                  "priority": priority})

    if not os.environ.get("ETHERSCAN_API_KEY"):
        diag["status"] = "skipped_no_api_key"
        diag["degraded_sources"] = ["arbitrum_l1"]
        print("[graph] ETHERSCAN_API_KEY absent - L1 frontier expansion SKIPPED. "
              "Graph is limited to locally recorded edges (typically depth 1).")
        return edges, diag

    from src.tracer import get_usdc_transfers

    deadline = time.monotonic() + budget["time_budget_seconds"]
    max_calls = budget["max_expansions"]
    max_depth = budget.get("max_depth", DEFAULTS["max_depth"])
    branching = budget.get("max_branching", DEFAULTS.get("max_branching", 8))
    target = target.lower()

    edges = list(edges)
    known_ids = {e["id"] for e in edges}
    dust_usd = budget.get("dust_usd", DEFAULTS["dust_usd"])
    # Configurable, but never unbounded: a missing or nonsensical config value
    # falls back to the module default rather than disabling the ceiling.
    max_queue = _positive_int(budget.get("max_frontier_queue"), MAX_FRONTIER_QUEUE)
    max_decisions = _positive_int(budget.get("max_decisions"), MAX_DECISIONS)
    max_ledger = _positive_int(budget.get("max_expanded_ledger"), MAX_EXPANDED_LEDGER)

    # Wallets fully expanded on an earlier run. Without this the frontier
    # re-walked the target's direct recipients every single run, so the lookup
    # budget was consumed by hop 1 forever and the deeper queue never drained.
    done = {(w or "").lower() for w in (already_expanded or []) if w}
    explored = set(done)
    calls = 0
    added = []
    stopped_reason = None

    # Seed: resumed work first, then the target's direct recipients. Both are
    # drawn from expandable edges only, so the frontier and build_graph agree on
    # what counts as a real transfer.
    walkable = _expandable_edges(edges, dust_usd)
    queue = []
    queue.extend((int(item.get("depth", 1)), w)
                 for item in (resume or [])
                 if (w := (item.get("wallet") or "").lower()) and w != target)
    queue.extend((1, e["dst"]) for e in walkable if e["src"] == target)

    services = detect_services(edges, {(a or "").lower()
                                       for a in (known_services or set())})

    expanded_now = set()
    try:
        depth = 1
        while depth <= max_depth:
            level = [(d, w) for d, w in queue
                     if d == depth and w not in explored and w != target]
            if not level:
                depth += 1
                continue

            ranked = []
            for d, w in sorted(set(level)):
                if w in services:
                    decide(w, d, "suppressed", f"service address: {services[w]}")
                    explored.add(w)
                    continue
                pr, prof = _frontier_priority(w, d, walkable, now_ts)
                ranked.append((pr, w, d, prof))
            # Deterministic: address breaks priority ties so two runs over the
            # same data expand the same wallets in the same order.
            ranked.sort(key=lambda x: (-x[0], x[1]))

            for pr, wallet, d, prof in ranked[:branching]:
                if calls >= max_calls:
                    stopped_reason = f"lookup budget ({max_calls}) exhausted"
                    decide(wallet, d, "deferred", stopped_reason, pr)
                    continue
                if time.monotonic() > deadline:
                    stopped_reason = (f"time budget "
                                      f"({budget['time_budget_seconds']}s) exhausted")
                    decide(wallet, d, "deferred", stopped_reason, pr)
                    continue
                calls += 1
                found = 0
                try:
                    rows = list(get_usdc_transfers(wallet))
                except Exception as exc:
                    # One address failing must not abandon the rest of the walk.
                    # The wallet stays OUT of `explored` so it is retried next
                    # run rather than being recorded as finished.
                    diag["partial_failures"].append(
                        {"wallet": wallet, "depth": d, "error": str(exc)[:120]})
                    diag["degraded_sources"] = ["arbitrum_l1"]
                    decide(wallet, d, "deferred", f"lookup failed: {str(exc)[:80]}", pr)
                    continue
                explored.add(wallet)
                expanded_now.add(wallet)
                diag["deepest_expanded"] = max(diag["deepest_expanded"], d)
                for tx in rows:
                    e = normalise_l1_transfer(tx)
                    if not e or e["id"] in known_ids:
                        continue
                    known_ids.add(e["id"])
                    added.append(e)
                    edges.append(e)
                    found += 1
                    if (float(e.get("amount_usd", 0) or 0) < dust_usd
                            and e.get("discovery_source") != SRC_GAS_FUNDING):
                        continue  # poisoning dust is never worth a lookup
                    walkable.append(e)
                    if e["src"] == wallet and d + 1 <= max_depth:
                        queue.append((d + 1, e["dst"]))
                decide(wallet, d, "expanded",
                       f"{found} new edge(s); relay={prof['relay']['is_relay']} "
                       f"({prof['relay']['reason']})", pr)

            for pr, wallet, d, _p in ranked[branching:]:
                decide(wallet, d, "deferred",
                       f"beyond branching limit ({branching}) at depth {d}", pr)
            # A wallet that just turned into a high-fan-degree hub must be
            # suppressed before its recipients are walked, not after.
            services = detect_services(edges, {(a or "").lower()
                                               for a in (known_services or set())})
            depth += 1
    except Exception as exc:  # partial results must survive a mid-run failure
        diag["status"] = "failed"
        diag["error"] = str(exc)[:200]
        diag["degraded_sources"] = ["arbitrum_l1"]
        print(f"[graph] frontier expansion FAILED after {calls} lookup(s): {exc}")

    # One entry per wallet at its SHALLOWEST outstanding depth. Keying on
    # (depth, wallet) counted a wallet reachable at two depths as two units of
    # remaining work, which both overstated `frontier_remaining` and re-queued
    # the same address twice.
    shallowest: dict[str, int] = {}
    for d, w in queue:
        if w in explored or w == target or w in services:
            continue
        if d > max_depth:
            continue
        if w not in shallowest or d < shallowest[w]:
            shallowest[w] = d

    # Rank the COMPLETE deduplicated frontier before the cap is applied, so what
    # survives truncation is the strongest chase target rather than whatever
    # sorted earliest by address. Ranking is affordable because the edge index
    # above makes it O(frontier) instead of O(frontier x edges).
    rank_index = EdgeIndex(walkable)
    scored = []
    for w, d in shallowest.items():
        pr, prof = _frontier_priority(w, d, rank_index, now_ts)
        scored.append((_frontier_rank_key(w, d, pr, prof), w, d, pr))
    scored.sort(key=lambda x: x[0])
    pending = [{"wallet": w, "depth": d, "priority": pr} for _k, w, d, pr in scored]

    diag["lookups"] = calls
    diag["new_edges"] = len(added)
    diag["wallets_expanded"] = sorted(expanded_now)
    diag["skipped_already_expanded"] = len(done)
    # Everything ever expanded, so the next run does not repeat finished work.
    diag["expanded_ledger"] = sorted(explored)[:max_ledger]
    diag["frontier_size"] = len(explored) + len(pending)
    # Eligible = deduplicated, in-depth, not already expanded, not a service.
    diag["frontier_eligible"] = len(pending)
    diag["frontier_remaining"] = len(pending)
    diag["frontier_queue"] = pending[:max_queue]
    diag["frontier_retained"] = len(diag["frontier_queue"])
    diag["frontier_cap"] = max_queue
    # Truncation has to be visible: silently dropping the tail of the queue
    # while still reporting the full remaining count makes lost work look done.
    # Anything dropped here is dropped PERMANENTLY — a queued wallet is only
    # known because its parent was expanded, and that parent is now in the
    # ledger and will never be expanded again to rediscover it.
    diag["frontier_truncated"] = max(0, len(pending) - max_queue)
    # `decisions` is per-wallet-per-level and grows with the frontier, so it has
    # to be bounded too or raising the queue cap inflates the file indirectly.
    if len(diag["decisions"]) > max_decisions:
        diag["decisions_truncated"] = len(diag["decisions"]) - max_decisions
        diag["decisions"] = diag["decisions"][:max_decisions]
    else:
        diag["decisions_truncated"] = 0

    failures = len(diag["partial_failures"])
    if diag["status"] != "failed":
        if failures and not expanded_now:
            # Every attempted lookup failed: that is an outage, not a partial
            # result, and must not read as a successful-but-thin expansion.
            diag["status"] = "failed"
            diag["error"] = diag["partial_failures"][0]["error"]
            diag["degraded_sources"] = ["arbitrum_l1"]
        elif stopped_reason or pending or failures:
            if stopped_reason:
                diag["status"] = "budget_exhausted"
                diag["stopped_reason"] = stopped_reason
            elif failures:
                diag["status"] = "partial"
                diag["stopped_reason"] = (
                    f"{failures} lookup(s) failed and were re-queued")
            else:
                diag["status"] = "partial"
                diag["stopped_reason"] = "frontier not fully drained"
        else:
            diag["status"] = "ok"
        diag["completed_at"] = utc_now()

    print(f"[graph] L1 expansion {diag['status']}: {calls} lookup(s) to depth "
          f"{diag['deepest_expanded']}, {len(added)} new edge(s), "
          f"{len(pending)} wallet(s) queued for next run")
    print(f"[graph] frontier: {diag['frontier_eligible']} eligible, "
          f"{diag['frontier_retained']} retained (cap {max_queue}), "
          f"{diag['frontier_truncated']} truncated — retained by chase priority")
    # NB: the expansion cursor is written by run_transfer_graph, not here.
    # Writing it from inside the traversal meant every unit test that called
    # expand_frontier mutated data/state/ in the working tree — a dry run must
    # never leave a production file behind.
    return edges, diag


def _read_previous_graph() -> dict:
    """Load the last saved graph, tolerating absence and a corrupt file.

    A truncated latest.json must degrade to "no history" rather than crash the
    run — losing the frontier is recoverable, losing the whole run is not.
    """
    path = DATA_DIR / "transfer_graph" / "latest.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        print(f"[graph] previous graph unreadable ({exc}) — starting from empty "
              f"history; frontier and delivered-alert state are reset")
        return {}


def run_transfer_graph(expand: bool = True) -> dict:
    """Full pipeline: gather edges, traverse, score, persist, alert."""
    config = load_config()
    target = config["target_wallet"].lower()
    cfg = {**DEFAULTS, **(config.get("transfer_graph") or {})}

    known_services = {a.lower() for a in config.get("excluded_addresses", [])}
    known_services.add(config["hl_bridge_contract"].lower())
    known_services.add(config["usdc_contract_arbitrum"].lower())
    known_services |= {a.lower() for a in config.get("known_service_addresses", [])}

    # Loaded ONCE, before expansion, and migrated to the current schema: it
    # carries the unfinished frontier, the ledger of finished expansions and the
    # delivered-alert state that the whole run depends on.
    previous_graph = migrate_graph(_read_previous_graph())

    edges = collect_known_edges()
    print(f"[graph] {len(edges)} edge(s) from local data")
    if expand:
        prev_expansion = ((previous_graph.get("health") or {})
                          .get("expansion") or {})
        resume = prev_expansion.get("frontier_queue") or []
        already = prev_expansion.get("expanded_ledger") or []
        if resume:
            print(f"[graph] resuming {len(resume)} wallet(s) queued by the "
                  f"previous run")
        if already:
            print(f"[graph] {len(already)} wallet(s) already expanded on an "
                  f"earlier run — not repeating")
        edges, expansion = expand_frontier(
            edges, target, cfg, resume=resume,
            known_services=known_services, already_expanded=already)
        write_cursor("transfer_graph_last_expansion_ms", now_ms())
    else:
        expansion = {"status": "disabled", "degraded_sources": ["arbitrum_l1"],
                     "attempted_at": utc_now()}
    # Carry the previous successful expansion forward so the dashboard can show
    # "last successful L1 expansion" even on a run where it was skipped.
    prev_health = (previous_graph.get("health") or {}).get("expansion") or {}
    if expansion.get("status") == "ok":
        expansion["last_successful"] = expansion.get("completed_at")
    elif prev_health.get("status") == "ok":
        expansion["last_successful"] = prev_health.get("completed_at")
    elif prev_health.get("last_successful"):
        expansion["last_successful"] = prev_health["last_successful"]
    # A run that did not expand must not erase what earlier runs finished.
    if expansion.get("status") in ("disabled", "skipped_no_api_key"):
        expansion.setdefault("expanded_ledger", prev_health.get("expanded_ledger") or [])
        expansion.setdefault("frontier_queue", prev_health.get("frontier_queue") or [])

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

    previous = previous_graph or None
    annotate_changes(graph, previous)

    alerts = select_alerts(graph, previous)
    if alerts:
        sent, undelivered = fire_alerts(graph, alerts)
    else:
        sent, undelivered = 0, []
    advance_alert_state(graph, previous, alerts, undelivered)
    graph["alerts_fired"] = sent
    graph["pending_alerts"] = len(alerts)
    # Persisted so the next run re-selects these. Without it the saved graph
    # advances as though the alert had been delivered, select_alerts sees no
    # change, and the discovery is silently dropped forever — which is exactly
    # what happened to a MIGRATION_CANDIDATE after an SMTP auth failure.
    graph["undelivered_alerts"] = undelivered
    if undelivered:
        print(f"[graph] {len(undelivered)} alert(s) undelivered and queued for retry")

    save_latest(str(DATA_DIR / "transfer_graph"), graph)

    by_class: dict[str, int] = {}
    for n in graph["nodes"]:
        by_class[n["classification"]] = by_class.get(n["classification"], 0) + 1
    print(f"[graph] {graph['node_count']} node(s), {graph['edge_count']} edge(s), "
          f"{graph['service_count']} service(s), depth {graph['max_depth_reached']}")
    for cls in sorted(by_class, key=lambda c: -CLASS_ORDER[c]):
        print(f"[graph]   {cls}: {by_class[cls]}")
    if alerts:
        # Never say "sent" for a delivery that failed. The old
        # "0/4 discovery alert(s) sent" read as a send having happened, when in
        # fact nothing left the process and four alerts were sitting in the
        # retry queue.
        attempted = len(alerts)
        delivered = graph["alerts_fired"]
        failed = len(graph["undelivered_alerts"])
        print(f"[graph] alerts: {attempted} attempted, {delivered} delivered, "
              f"{failed} failed, {len(graph['undelivered_alerts'])} queued for retry")
    return graph


def main():
    run_transfer_graph()


if __name__ == "__main__":
    main()
