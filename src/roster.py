# src/roster.py
"""One ranked view of every wallet plausibly belonging to the target.

Five detectors run independently and each writes its own file: the transfer
graph, the HL-native ledger analyser, the deposit/withdrawal correlator, the
behavioural scanner and the HyperEVM watcher. Read one at a time they answer
different questions, and a wallet showing up weakly in three of them looks
weaker than a wallet showing up strongly in one.

That is backwards, and it is the whole reason this exists. Independent vectors
agreeing is the strongest evidence this project can produce, because the ways
they can be fooled do not overlap: an amount coincidence does not also fake a
shared deposit address, and a shared deposit address does not also fake a
trading style. The roster's job is to count them.

Nothing here re-derives evidence. It reads what the detectors already wrote,
attributes each wallet to the vectors supporting it, and tiers on that count —
so a change inside one detector cannot silently move a tier here.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, load_config, save_latest

ROSTER_DIR = DATA_DIR / "roster"

# Named so a tier says WHY, not just how confident. A wallet supported by two
# independent vectors is qualitatively different from one supported twice by the
# same vector, and only the former can reach the top tier.
VECTOR_TRANSFER = "transfer"        # observed on-chain or HL-native movement
VECTOR_LINKAGE = "linkage"          # shared funder or shared deposit address
VECTOR_CORRELATION = "correlation"  # exit amount re-appeared as a deposit
VECTOR_BEHAVIOURAL = "behavioural"  # trades like the target
VECTOR_HL_NATIVE = "hl_native"      # two-way flow entirely inside Hyperliquid
# Strongest of all: an agent is an address an account EXPLICITLY authorised to
# trade for it, so two accounts sharing one are the same operator. Not a
# coincidence of flow or of style but a deliberate act of control.
VECTOR_AGENT = "shared_agent"
# Hyperliquid itself declaring the relationship: an address that is a cluster
# wallet's agent or sub-account, or its declared staking partner. Like a
# shared agent, an act of control rather than an inference.
VECTOR_EXPLICIT = "explicit_link"
# One wallet goes quiet, another is born. The only vector that needs NO
# connection between the two, which is exactly why it has to count: a wallet
# funded from somewhere unobservable leaves nothing else to find it by. It
# fired a CRITICAL alert from the day it was built and cast no vote here, so
# `0xdd53c529` — born two days into a six-day silence, $999 to $51.3M in three
# weeks, and independently an amount-correlation match — sat at POSSIBLE on
# one vector.
VECTOR_DORMANCY = "dormancy_handoff"

TIER_CONFIRMED = "CONFIRMED"
TIER_PROBABLE = "PROBABLE"
TIER_POSSIBLE = "POSSIBLE"
TIER_WATCH = "WATCH"
TIER_INFRASTRUCTURE = "INFRASTRUCTURE"

TIER_ORDER = {TIER_CONFIRMED: 0, TIER_PROBABLE: 1, TIER_POSSIBLE: 2,
              TIER_WATCH: 3, TIER_INFRASTRUCTURE: 4}


def _read(path: Path, key: str) -> list:
    """A detector's output, or an empty list if it never ran or is unreadable.

    A missing detector must not take down the roster: the point of five vectors
    is that four still say something.
    """
    try:
        with open(path) as f:
            return json.load(f).get(key) or []
    except (OSError, ValueError, AttributeError):
        return []


def assign_tier(vectors: set, confidence: float, is_service: bool,
                known_self: bool) -> str:
    """Tier from the NUMBER of independent vectors, not from any one score.

    `known_self` is operator ground truth from config and outranks measurement.

    The top tier requires two independent vectors because each vector has a
    known way of being wrong alone: an amount match can be coincidence — measured
    live, three wallets matched on amount and every one was style-vetoed as a
    different trader — a transfer can be a payment to a stranger, and behavioural
    similarity is only as good as a scorer that currently cannot pick the target
    out of a lineup.
    """
    if is_service:
        return TIER_INFRASTRUCTURE
    if known_self:
        return TIER_CONFIRMED
    # A shared agent is a deliberate act of control by the account owner, not an
    # inference from flow. It is the one signal strong enough to stand alone.
    if VECTOR_AGENT in vectors or VECTOR_EXPLICIT in vectors:
        return TIER_CONFIRMED
    if len(vectors) >= 2 and confidence >= 0.60:
        return TIER_CONFIRMED
    if len(vectors) >= 2 or confidence >= 0.60:
        return TIER_PROBABLE
    if confidence >= 0.40 or vectors:
        return TIER_POSSIBLE
    return TIER_WATCH


def behavioural_is_trustworthy() -> bool:
    """Whether the behavioural scorer has proven it can identify the target.

    The self-match backtest scores the trader's own recent window against his
    own fingerprint and ranks it among strangers. It FAILS today: he reaches
    rank 1 but not by the required +0.05 margin. A scorer that cannot pick the
    target out of a lineup by a clear margin cannot be evidence that some other
    wallet is him.

    Deliberately no snapshot figures here. The margin is a property of the
    LINEUP as much as of the scorer — measured over three consecutive runs with
    the scorer unchanged it went +0.0361, +0.0369, +0.0097 while his own score
    moved 0.0016, because a closer-matching stranger turned up. A number quoted
    in a docstring goes stale within a day and invites exactly the reweighting
    CLAUDE.md rule 4 forbids. Read `profile/backtest.json`.

    So while it fails, behavioural similarity is recorded as context but casts
    no vote. Counting it would be the roster's whole premise inverted: the tiers
    exist to reward INDEPENDENT vectors agreeing, and a vector known to be
    unreliable is not independent evidence, it is noise with a number attached.

    This also removes the need to trust stored candidate records that predate
    the current scorer — one such record carried `vetoes: []` and the tier
    "CONFIRMED_CANDIDATE", which the current threshold scheme does not define,
    for a wallet that scores 0.45 with a style veto when re-scored live.
    """
    try:
        with open(DATA_DIR.parent / "profile" / "backtest.json") as f:
            return bool(json.load(f).get("passed"))
    except (OSError, ValueError, AttributeError):
        return False


def build_roster(config: dict | None = None) -> dict:
    """Merge every detector's output into one ranked roster."""
    config = config or load_config()
    target = (config.get("target_wallet") or "").lower()
    known_self = {(w or "").lower() for w in config.get("known_self_wallets", [])}

    trust_behavioural = behavioural_is_trustworthy()
    wallets: dict[str, dict] = {}

    def entry(addr: str) -> dict:
        a = (addr or "").lower()
        return wallets.setdefault(a, {
            "wallet": a, "vectors": set(), "confidence": 0.0,
            "classification": None, "reasons": [], "evidence": {},
            "is_service": False, "known_self": a in known_self,
        })

    for node in _read(DATA_DIR / "transfer_graph" / "latest.json", "nodes"):
        a = (node.get("wallet") or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["classification"] = node.get("classification")
        e["confidence"] = max(e["confidence"], float(node.get("confidence") or 0))
        e["reasons"] = list(node.get("confidence_reasons") or [])
        ev = node.get("evidence") or {}
        e["is_service"] = bool(ev.get("is_service"))
        e["evidence"]["service_reason"] = ev.get("service_reason")
        e["evidence"]["totals"] = node.get("totals") or {}
        e["evidence"]["depth"] = node.get("depth")
        e["evidence"]["chains"] = node.get("chains") or []
        # An inferred correlation edge is not an observed transfer, so it must
        # not count as the transfer vector — that would let one detector supply
        # two supposedly independent votes.
        if ev.get("transfer_count"):
            e["vectors"].add(VECTOR_TRANSFER)
        if (ev.get("shared_deposit_address") or ev.get("shared_funder")
                or ev.get("gas_funded_by_target")):
            e["vectors"].add(VECTOR_LINKAGE)
        if ev.get("hl_native") and ev.get("bidirectional"):
            e["vectors"].add(VECTOR_HL_NATIVE)

    for party in _read(DATA_DIR / "hl_transfers" / "latest.json", "counterparties"):
        a = (party.get("wallet") or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["evidence"]["hl_out_usd"] = party.get("total_out_usd")
        e["evidence"]["hl_in_usd"] = party.get("total_in_usd")
        if party.get("bidirectional"):
            e["vectors"].add(VECTOR_HL_NATIVE)

    for match in _read(DATA_DIR / "correlations" / "latest.json", "matches"):
        a = (match.get("wallet") or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["vectors"].add(VECTOR_CORRELATION)
        prev = e["evidence"].get("correlation_confidence") or 0
        if float(match.get("confidence") or 0) > float(prev):
            e["evidence"]["correlation_confidence"] = match.get("confidence")
            e["evidence"]["correlation_gap_hours"] = match.get("gap_hours")
            e["evidence"]["competing_deposits"] = match.get("competing_deposits")

    for cand in _read(DATA_DIR / "candidates" / "latest.json", "candidates"):
        a = (cand.get("wallet") or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        score = float(cand.get("latest_score") or 0)
        e["evidence"]["behavioural_score"] = score
        e["evidence"]["behavioural_tier"] = cand.get("latest_tier")
        vetoes = ((cand.get("latest_evidence") or {}).get("vetoes")) or []
        e["evidence"]["style_vetoes"] = vetoes
        # A style veto is a positive finding that this is a DIFFERENT human, so
        # a vetoed wallet must not also cast a behavioural vote for being the
        # same one.
        if score >= 0.65 and not vetoes and trust_behavioural:
            e["vectors"].add(VECTOR_BEHAVIOURAL)

    # Wallets sharing an authorised agent with the target.
    try:
        with open(DATA_DIR / "agent_links" / "latest.json") as f:
            linked = json.load(f).get("linked_to_target") or {}
    except (OSError, ValueError, AttributeError):
        linked = {}
    for addr, agents in linked.items():
        a = (addr or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["vectors"].add(VECTOR_AGENT)
        e["evidence"]["shared_agents"] = agents

    # Portfolio overlap is recorded but is NOT a vector. A copy-trader holds the
    # same basket in the same direction at the same time by definition, and this
    # project exists because its owner copies this trader by hand — so a high
    # score is exactly as consistent with a copycat as with him. It informs a
    # human reading a lead; it must not promote one.
    try:
        with open(DATA_DIR / "portfolio_overlap" / "latest.json") as f:
            overlaps = json.load(f).get("overlaps") or {}
    except (OSError, ValueError, AttributeError):
        overlaps = {}
    for addr, detail in overlaps.items():
        a = (addr or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["evidence"]["portfolio_overlap"] = detail.get("score")
        e["evidence"]["shared_rare_markets"] = [
            s.get("market") for s in (detail.get("shared_rare") or [])][:5]

    # Hyperliquid's own answers about who an address is: agent owners,
    # sub-account masters, staking links, the current frontend agent, presence
    # and birth. Links to the cluster confirm alone.
    try:
        with open(DATA_DIR / "identity" / "latest.json") as f:
            ident_doc = json.load(f)
    except (OSError, ValueError, AttributeError):
        ident_doc = {}
    for link in ident_doc.get("links") or []:
        for side in ("address", "linked_to"):
            a = (link.get(side) or "").lower()
            if not a or a == target:
                continue
            e = entry(a)
            e["vectors"].add(VECTOR_EXPLICIT)
            e["evidence"].setdefault("explicit_links", []).append(
                {"kind": link.get("kind"), "with": link.get("linked_to")
                 if side == "address" else link.get("address")})
    for a, ident in (ident_doc.get("identities") or {}).items():
        a = (a or "").lower()
        if not a or a == target or a not in wallets:
            continue
        e = entry(a)
        e["evidence"]["hl_role"] = ident.get("role")
        e["evidence"]["hl_account_value"] = ident.get("account_value")
        e["evidence"]["hl_birth_ms"] = ident.get("birth_ms")
        if ident.get("agent_address"):
            e["evidence"]["frontend_agent"] = ident["agent_address"]

    # A wallet whose FIRST activity lands inside an unusual silence of his.
    # Independent of every other vector by construction: it needs no transfer,
    # no shared address and no style resemblance, so it cannot fail the same
    # way any of them do.
    try:
        with open(DATA_DIR / "dormancy" / "latest.json") as f:
            handoffs = json.load(f).get("handoffs") or {}
    except (OSError, ValueError, AttributeError):
        handoffs = {}
    for a, h in handoffs.items():
        a = (a or "").lower()
        if not a or a == target or not (h or {}).get("score"):
            continue
        e = entry(a)
        e["vectors"].add(VECTOR_DORMANCY)
        e["evidence"]["dormancy_handoff"] = {
            k: h.get(k) for k in ("score", "gap_length", "delay_days",
                                  "candidate_first_day")}

    # Lead/lag co-movement: evidence, and the one behavioural reading a copier
    # cannot fake. A `same_hand` verdict still needs an independent vector.
    try:
        with open(DATA_DIR / "comovement" / "latest.json") as f:
            comove = json.load(f).get("results") or {}
    except (OSError, ValueError, AttributeError):
        comove = {}
    for a, r in comove.items():
        a = (a or "").lower()
        if not a or a == target or r.get("verdict") in (None, "untestable"):
            continue
        e = entry(a)
        e["evidence"]["comovement"] = {k: r.get(k) for k in
                                       ("verdict", "pairs", "lead_share", "excess",
                                        "median_lag_min")}

    for acct in _read(DATA_DIR / "hyperevm" / "latest.json", "wallets"):
        a = (acct.get("address") or "").lower()
        if not a or a == target:
            continue
        e = entry(a)
        e["evidence"]["hyperevm_nonce"] = acct.get("nonce")

    rows = []
    for e in wallets.values():
        e["vectors"] = sorted(e["vectors"])
        e["vector_count"] = len(e["vectors"])
        e["tier"] = assign_tier(set(e["vectors"]), e["confidence"],
                                e["is_service"], e["known_self"])
        rows.append(e)

    rows.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 9),
                             -r["vector_count"], -r["confidence"], r["wallet"]))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1

    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target": target,
        "wallet_count": len(rows),
        # Recorded so a reader can tell a roster with four working vectors from
        # one with five, rather than wondering why nothing is behavioural.
        "behavioural_counts_as_a_vector": trust_behavioural,
        "tier_counts": counts,
        "wallets": rows,
    }


def main() -> int:
    roster = build_roster()
    save_latest(str(ROSTER_DIR), roster)
    print(f"[roster] {roster['wallet_count']} wallet(s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(
              roster["tier_counts"].items(),
              key=lambda kv: TIER_ORDER.get(kv[0], 9))))
    shown = 0
    for row in roster["wallets"]:
        if row["tier"] == TIER_INFRASTRUCTURE or shown >= 8:
            continue
        shown += 1
        print(f"[roster]   {row['wallet'][:14]}... {row['tier']:<12} "
              f"conf {row['confidence']:.2f}  "
              f"vectors: {', '.join(row['vectors']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
