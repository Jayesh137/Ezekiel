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


def pinned_wallets(config: dict | None) -> list[str]:
    """Wallets no cap may ever drop: the operator's watch list, then his own.

    `config.watch_wallets` is a question a human asked by hand, and
    `known_self_wallets` is ground truth. Neither is an inference the roster is
    entitled to rank.
    """
    config = config or {}
    target = ((config.get("target_wallet") or "").strip().lower())
    out, seen = [], {target} if target else set()
    for group in (config.get("watch_wallets") or [],
                  config.get("known_self_wallets") or []):
        for entry in group:
            if isinstance(entry, str):
                entry = {"address": entry}
            if not isinstance(entry, dict):
                continue
            address = (entry.get("address") or "").strip().lower()
            if address and address not in seen:
                seen.add(address)
                out.append(address)
    return out


def detector_candidates(config: dict | None, roster: dict | None,
                        limit: int) -> list[str]:
    """The wallets a per-wallet detector should ask about this run.

    The roster ranks by how much evidence a wallet ALREADY has, so taking the
    first N of it to decide where to LOOK for evidence inverts the search. A
    wallet with no vectors sorts below every wallet with one, and a wallet he
    has just migrated to has no vectors by construction — it is the newest,
    quietest, least-connected thing on the list, which is exactly the shape the
    ranking puts last.

    Measured live 2026-09-12: `0xdd53c529…`, the only wallet in
    `config.watch_wallets`, under close watch since the 11th and graded PROBABLE
    on amount and timing, sat at position 166 of 180 non-infrastructure rows and
    was cut by all four detector caps of 40. The cut-off was a wallet carrying
    confidence 0.0311. So dormancy — the vector added FOR that wallet, whose own
    docstring names it — had never once scored it, the shared-agent vector had
    never seen its named agent `0x1e8695b7…`, and both absences read in the
    stored files as a measured "no".

    So the pinned wallets go first and are never trimmed: the cap exists to
    protect an API budget, and silence about a wallet the operator named by hand
    is not a saving. Everything after them keeps roster order, where the ranking
    is doing the job it is good at — spending a bounded budget on the strongest
    of the wallets nobody has vouched for.
    """
    picked = pinned_wallets(config)
    seen = set(picked)
    target = ((config or {}).get("target_wallet") or "").strip().lower()
    if target:
        seen.add(target)
    rows = (roster or {}).get("wallets")
    room = max(0, int(limit) - len(picked))
    for row in rows if isinstance(rows, list) else []:
        if room <= 0:
            break
        if not isinstance(row, dict):
            continue
        address = (row.get("wallet") or "").strip().lower()
        if not address or address in seen:
            continue
        if row.get("tier") == TIER_INFRASTRUCTURE or row.get("is_service"):
            continue
        seen.add(address)
        picked.append(address)
        room -= 1
    return picked


def load_roster() -> dict:
    """The stored roster, or an empty one. A detector must still run without it."""
    try:
        with open(ROSTER_DIR / "latest.json") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evidence_strength(row: dict) -> float:
    """The best score any vector gives this wallet, for RANKING only.

    `confidence` comes from the transfer graph alone, so a wallet reached by any
    other vector carries 0.0 — rule 6 inside the ranking, a missing reading
    priced as zero and invisible to every comparison. Measured live 2026-09-12:
    124 of 181 non-infrastructure wallets sat at exactly 0.0 and 75 of them tied
    inside POSSIBLE, so the sort fell through to the wallet ADDRESS and leading
    hex digits decided which leads a bounded budget looked at. A correlation
    lead at 0.9974 survived the cap because it begins `0x7f`; one at 0.6839 was
    cut at #130, holding 33 live named agents nothing had indexed.

    These are different scales and the maximum of them is NOT a confidence — it
    must never be stored as one or compared against a threshold. It is a chase
    priority: a number whose only job is to put the strongest thing we know
    about a wallet ahead of the weakest thing we know about another.
    """
    evidence = row.get("evidence") or {}
    scores = [_as_float(row.get("confidence")),
              _as_float(evidence.get("correlation_confidence")),
              _as_float((evidence.get("dormancy_handoff") or {}).get("score")),
              _as_float(evidence.get("portfolio_overlap"))]
    return max(scores)


def rank_key(row: dict):
    """Chase priority: tier, then agreeing vectors, then strength, then address.

    Tier and vector COUNT stay primary — independent vectors agreeing is the
    project's whole premise, and one loud score must not outrank two quiet ones
    that corroborate. The address remains last, but only as a deterministic
    tiebreaker between wallets we genuinely know the same amount about.
    """
    return (TIER_ORDER.get(row.get("tier"), 9),
            -int(row.get("vector_count") or 0),
            -evidence_strength(row),
            row.get("wallet") or "")


def attach_rank_strength(rows: list) -> None:
    """Record on each row the strength that decided its position.

    Serialised so the ordering is legible rather than mysterious: a reader
    seeing a correlation lead at rank 2 with no transfer-graph confidence needs
    to be told what put it there. Emphatically NOT a confidence — see
    `evidence_strength`.
    """
    for row in rows:
        row["rank_strength"] = round(evidence_strength(row), 4)


def carry_peak_tier(rows: list, previous: dict | None) -> None:
    """Mark each row with the best tier it has ever held, and any fall from it.

    The roster is rebuilt from scratch every run so that a change inside one
    detector cannot silently move a tier. The cost is that a wallet whose
    evidence LAPSES is rewritten as though it never had any, and nothing in the
    file distinguishes the two.

    Measured live 2026-09-12: `0xdd53c529…` held amount-correlation and dormancy,
    a pool refactor destroyed the bridge pool's stored answer, and the wallet
    fell PROBABLE → WATCH. At WATCH it sorted 166th of 180 and was cut by all
    four detector caps of 40, so dormancy stopped scoring it too and the last
    vector went as well. A wallet under close watch by name became
    indistinguishable from 165 strangers, and no file recorded that anything
    had moved.

    Rule 5 over time: "we no longer have the evidence" is not "there was never
    anything here". Recorded, never alerted — a lost inference is a fact about
    OUR coverage, not a contact with his world.
    """
    prior = {}
    for row in ((previous or {}).get("wallets") or []):
        if not isinstance(row, dict):
            continue
        address = (row.get("wallet") or "").lower()
        if not address:
            continue
        seen = [t for t in (row.get("peak_tier"), row.get("tier")) if t in TIER_ORDER]
        if seen:
            prior[address] = min(seen, key=lambda t: TIER_ORDER[t])

    for row in rows:
        tier = row.get("tier")
        was = prior.get((row.get("wallet") or "").lower())
        best = tier
        if was in TIER_ORDER and tier in TIER_ORDER:
            best = min((was, tier), key=lambda t: TIER_ORDER[t])
        row["peak_tier"] = best
        # Grading a wallet INFRASTRUCTURE is a measurement that outranks every
        # inference — `0xd7a827fb…` was POSSIBLE until its 590,836 Arbitrum
        # transactions were counted. Reporting that as a demotion inverts it.
        dropped = (tier != TIER_INFRASTRUCTURE and best != tier
                   and TIER_ORDER.get(best, 9) < TIER_ORDER.get(tier, 9))
        row["tier_dropped_from"] = best if dropped else None


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

    carry_peak_tier(rows, load_roster())
    attach_rank_strength(rows)

    rows.sort(key=rank_key)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    demoted = [r for r in rows if r.get("tier_dropped_from")]

    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target": target,
        "wallet_count": len(rows),
        # Recorded so a reader can tell a roster with four working vectors from
        # one with five, rather than wondering why nothing is behavioural.
        "behavioural_counts_as_a_vector": trust_behavioural,
        "tier_counts": counts,
        # A wallet that LOST a vector since the last run. Recorded, not alerted.
        "demoted_count": len(demoted),
        "demoted": [{"wallet": r["wallet"], "tier": r["tier"],
                     "was": r["tier_dropped_from"]} for r in demoted[:20]],
        "wallets": rows,
    }


def main() -> int:
    roster = build_roster()
    save_latest(str(ROSTER_DIR), roster)
    print(f"[roster] {roster['wallet_count']} wallet(s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(
              roster["tier_counts"].items(),
              key=lambda kv: TIER_ORDER.get(kv[0], 9))))
    for d in roster.get("demoted") or []:
        # Not an alert: a lost inference is a fact about our coverage, not about
        # him. It belongs in the run log where a stalled vector shows up.
        print(f"[roster]   DEMOTED {d['wallet'][:14]}... {d['was']} -> {d['tier']} "
              f"(a vector it used to have is no longer supported)")
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
