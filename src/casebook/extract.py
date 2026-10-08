"""A roster row -> casebook evidence items, and who is admitted (spec 2026-10-08 §6). Pure.

One code path serves the live update and the history backfill: each hands this
module a roster row exactly as the roster wrote it that day. Old roster versions
carry fewer evidence keys, so every read is tolerant. An absent key yields no
item, never an item holding zeros (rule 6).

An item here is the observation only: key, kind, strength, facts and summary.
Its family, band and weight come from model.py when the case is scored, so a
model revision re-scores every case without re-reading any history.

Generated text is ASCII on purpose: a case is read in a year on whatever console
is to hand.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from src import not_wallets
from src.casebook import model

ADDRESS = re.compile(r"0x[0-9a-f]{40}")
ADMITTING_TIERS = ("CONFIRMED", "PROBABLE", "POSSIBLE")
MAX_LIST = 5
MAX_KEYS = 20
MAX_TEXT = 200
# The transfer vote's own bar (roster.SELF_FLOW_MIN_USD): below it each way,
# two-way flow is dust and not a relationship.
TWO_WAY_MIN_USD = 1_000.0


def _addr(value) -> str:
    return str(value or "").strip().lower()


def _num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def bound(value, depth: int = 0):
    """A JSON-safe copy small enough to keep forever: strings <= 200 characters,
    lists <= 5, dicts <= 20 keys, nesting <= 4, non-finite floats as None."""
    if depth > 4:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, dict):
        return {str(k)[:60]: bound(v, depth + 1) for k, v in list(value.items())[:MAX_KEYS]}
    if isinstance(value, (list, tuple, set)):
        return [bound(v, depth + 1) for v in list(value)[:MAX_LIST]]
    return str(value)[:MAX_TEXT]


def usd(value) -> str:
    number = _num(value)
    return "an unpriced amount" if number is None else f"${number:,.0f}"


def short(address) -> str:
    a = _addr(address)
    return a[:10] + "..." if ADDRESS.fullmatch(a) else (a or "?")


def day_of(stamp) -> str | None:
    """YYYY-MM-DD from seconds, milliseconds or ISO text; None when unreadable."""
    if isinstance(stamp, bool) or stamp is None:
        return None
    if isinstance(stamp, (int, float)):
        seconds = stamp / 1000 if stamp > 1e11 else stamp
        try:
            return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(stamp)
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None


def cluster_of(config: dict) -> set[str]:
    """His wallets: the target and the configured known ones (ground truth)."""
    out = {_addr(config.get("target_wallet"))}
    out |= {_addr(w) for w in config.get("known_self_wallets") or []}
    return {a for a in out if a}


def pinned(config: dict) -> dict[str, str]:
    """Addresses the operator named, and why; ground truth first."""
    out: dict[str, str] = {}
    for key, label in (("known_self_wallets", "config:known_self"),
                       ("watch_wallets", "config:watch"), ("study_wallets", "config:study")):
        for entry in config.get(key) or []:
            address = _addr(entry.get("address") if isinstance(entry, dict) else entry)
            if ADDRESS.fullmatch(address):
                out.setdefault(address, label)
    out.pop(_addr(config.get("target_wallet")), None)
    return out


def ruling_of(config: dict, address: str) -> dict | None:
    for key, value in (config.get("casebook_rulings") or {}).items():
        if _addr(key) == address and isinstance(value, dict):
            return value
    return None


def extract_items(row: dict, config: dict) -> tuple[list[dict], set[str]]:
    """(items, refutes): the row's evidence as casebook items, and the kinds this
    reading positively re-measured and did not find (a refutation, spec §6.3)."""
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    vectors = {str(v) for v in (row.get("vectors") or [])}
    own = vectors - {str(v) for v in (evidence.get("vectors_via_group") or [])}
    cluster = cluster_of(config)
    target = _addr(config.get("target_wallet"))
    address = _addr(row.get("wallet"))
    reasons = [str(r)[:MAX_TEXT] for r in (row.get("reasons") or []) if r][:MAX_LIST]
    items: list[dict] = []
    refutes: set[str] = set()

    def add(kind, facts, summary, strength=None):
        items.append({"key": kind, "kind": kind, "strength": _num(strength),
                      "facts": bound(facts), "summary": str(summary)[:MAX_TEXT]})

    def rows_of(key):
        value = evidence.get(key)
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    def largest(rows, *keys):
        values = [_num(r.get(k)) for r in rows for k in keys]
        values = [v for v in values if v is not None]
        return max(values) if values else None

    # Protocol control: only a link to one of HIS wallets is evidence about him.
    links: dict[str, list] = {}
    for link in rows_of("explicit_links"):
        other = _addr(link.get("with"))
        if other in cluster:
            links.setdefault(str(link.get("kind")), []).append({"kind": link.get("kind"), "with": other})
    master = _addr(evidence.get("subaccount_of"))
    if master in cluster and not any(x["with"] == master for x in links.get("subaccount", [])):
        links.setdefault("subaccount", []).append({"kind": "subaccount", "with": master})
    for link_kind, kind, words in (("subaccount", "subaccount_of_cluster", "Sub-account link with his wallet"),
                                   ("agent", "agent_of_cluster", "Agent link with his wallet"),
                                   ("staking_link", "staking_link", "Staking link with his wallet")):
        if links.get(link_kind):
            add(kind, {"links": links[link_kind]}, f"{words} {short(links[link_kind][0]['with'])}")
    agents = evidence.get("shared_agents")
    if agents:
        listed = agents if isinstance(agents, list) else [agents]
        add("shared_agent", {"agents": listed}, f"Shares an authorised agent with him ({len(listed)})")

    # Money with his wallets.
    totals = evidence.get("totals") if isinstance(evidence.get("totals"), dict) else {}
    received = _num(totals.get("received_from_target_usd"))
    sent = _num(totals.get("sent_to_target_usd"))
    moved = [v for v in (received, sent) if v is not None]
    flows = rows_of("circle_flows")
    boundary = rows_of("boundary")
    voting = [b for b in boundary if b.get("vote") == "transfer"]
    deposit_votes = [b for b in boundary if b.get("vote") == "linkage"]
    other_boundary = [b for b in boundary if b.get("vote") not in ("transfer", "linkage")]
    valued = [v for v in moved if v >= TWO_WAY_MIN_USD]
    if "transfer" in own and (valued or not (flows or voting)):
        if valued:
            summary = f"Moved money with the target (received {usd(received)}, sent {usd(sent)})"
        else:
            # The vote came through an unpriced movement or through his config
            # wallets: $0 with the target is not the amount (rule 6), so say what
            # was observed instead.
            edges, unpriced = _num(totals.get("edge_count")), _num(totals.get("unvalued_edge_count"))
            counted = f"{edges:.0f} movement{'' if edges == 1 else 's'}" if edges else ""
            if counted and unpriced:
                counted += f", {unpriced:.0f} unpriced"
            party = ("the target" if any("target wallet" in r.lower() for r in reasons)
                     else "one of his wallets")
            summary = f"Observed transfer with {party}" + (f" ({counted})" if counted else "")
        add("direct_transfer", {"totals": totals, "depth": evidence.get("depth"),
                                "chains": evidence.get("chains"), "reasons": reasons},
            summary, max(valued) if valued else None)
    if "hl_native" in own or (received is not None and sent is not None
                              and min(received, sent) >= TWO_WAY_MIN_USD):
        add("two_way_flow", {"received_from_target_usd": received, "sent_to_target_usd": sent,
                             "hl_in_usd": evidence.get("hl_in_usd"),
                             "hl_out_usd": evidence.get("hl_out_usd")},
            "Money both ways with him" + (" inside Hyperliquid" if "hl_native" in own else ""),
            min(moved) if len(moved) == 2 else None)
    funder = _addr(evidence.get("shared_first_funder"))
    gas = [r for r in reasons
           if "target" in r.lower() and ("gas" in r.lower() or "funded directly" in r.lower())]
    if (funder and funder == target) or gas:
        add("funded_by_target", {"funder": funder or None, "reasons": gas},
            "Its first funding or gas came straight from the target wallet")
    if flows:
        add("circle_flow", {"count": len(flows), "flows": flows},
            f"{len(flows)} Circle transfer(s) with his wallets, largest "
            f"{usd(largest(flows, 'amount_usd'))}", largest(flows, "amount_usd"))
    if voting:
        add("boundary_transfer", {"count": len(voting), "rows": voting},
            f"Protocol record of money with his world, largest "
            f"{usd(largest(voting, 'amount_usd', 'usd'))}", largest(voting, "amount_usd", "usd"))
    if deposit_votes:
        add("boundary_deposit_address", {"count": len(deposit_votes), "rows": deposit_votes},
            f"Paid one of his private deposit addresses (protocol record), largest "
            f"{usd(largest(deposit_votes, 'amount_usd', 'usd'))}",
            largest(deposit_votes, "amount_usd", "usd"))
    if other_boundary:
        add("boundary_member", {"count": len(other_boundary), "rows": other_boundary},
            "Holds money that passed through his world (boundary record)",
            largest(other_boundary, "amount_usd", "usd"))
    reach = evidence.get("trace_reach")
    if isinstance(reach, dict):
        add("trace_reach", reach, f"His money reached it through quiet hops ({usd(reach.get('in_usd'))})",
            reach.get("in_usd"))

    # Shared private infrastructure.
    deposit = evidence.get("shared_private_deposit_address")
    if isinstance(deposit, dict):
        when = day_of(deposit.get("first_ts"))
        add("private_deposit_address", deposit,
            f"Paid his private deposit address {short(deposit.get('sentinel'))} "
            f"{usd(deposit.get('usd'))}" + (f" ({when})" if when else ""), deposit.get("usd"))
    for key, kind, words in (("shared_hl_deposit_address", "hl_deposit_address",
                              "Paid his Hyperliquid deposit address"),
                             ("shared_quiet_payee", "quiet_payee",
                              "Paid a quiet wallet his wallet also paid,")):
        value = evidence.get(key)
        if isinstance(value, dict):
            add(kind, value, f"{words} {short(value.get('via'))} {usd(value.get('outsider_usd'))}",
                value.get("outsider_usd"))
    if funder and funder != target:
        # Quiet is the roster's rule since 2026-09-16, not before: whether the funder
        # is quiet is re-judged against the whole chain (cases.rejudge), never claimed.
        add("quiet_first_funder", {"funder": funder}, f"Shares his first funder {short(funder)}")
    explained = bool(funder or gas or isinstance(deposit, dict) or deposit_votes or any(
        isinstance(evidence.get(k), dict) for k in ("shared_hl_deposit_address", "shared_quiet_payee")))
    if "linkage" in own and not explained:
        add("linkage_graph", {"reasons": reasons},
            reasons[0] if reasons else "Shares a deposit address or funder with him")

    # Custody gap.
    confidence = _num(evidence.get("correlation_confidence"))
    if confidence is not None:
        gap = _num(evidence.get("correlation_gap_hours"))
        add("amount_correlation", {"confidence": confidence, "gap_hours": gap,
                                   "competing_deposits": evidence.get("competing_deposits")},
            "An exit of his re-appeared as its deposit"
            + (f" {gap:.0f}h later" if gap is not None else "") + f" (confidence {confidence:.2f})",
            confidence)

    # Lifecycle.
    handoff = evidence.get("dormancy_handoff")
    score = _num(handoff.get("score")) if isinstance(handoff, dict) else None
    if score:
        add("dormancy_handoff", handoff,
            f"First active {handoff.get('delay_days')} day(s) into a {handoff.get('gap_length')}-day "
            f"silence of his (handoff score {score:.2f})", score)

    # Tooling.
    study = evidence.get("study") if isinstance(evidence.get("study"), dict) else None
    families = (study or {}).get("families")
    tooling = families.get("tooling") if isinstance(families, dict) else None
    if isinstance(tooling, dict):
        verdict, lr = tooling.get("verdict"), _num(tooling.get("lr"))
        facts = {"verdict": verdict, "lr": lr, "by": tooling.get("by"), "basis": tooling.get("basis"),
                 "key": tooling.get("key"), "as_of": study.get("as_of"),
                 "coverage_days": study.get("coverage_days")}
        if verdict in ("for", "mixed", "against") and lr is not None and lr > 0:
            add("study_tooling", facts, f"Candidate study: tooling {verdict} (likelihood ratio {lr:.3g})", lr)
        else:
            add("study_context", facts, f"Candidate study: tooling {verdict or 'not judged'}")
            if verdict == "neutral":
                refutes.add("study_tooling")
    program = evidence.get("execution_program")
    if isinstance(program, dict):
        add("execution_program", program,
            f"Reproduces {program.get('clips_matched')} of his per-coin clip sizes (execution program)",
            program.get("clip_match_ratio"))

    # Behaviour.
    behavioural = _num(evidence.get("behavioural_score"))
    if "behavioural" in own:
        add("behavioural_vote", {"score": behavioural, "threshold": evidence.get("behavioural_threshold")},
            "Trades like him (validated behavioural scorer)", behavioural)
    elif behavioural is not None:
        add("behavioural_score", {"score": behavioural, "tier": evidence.get("behavioural_tier"),
                                  "scorer_current": evidence.get("behavioural_scorer_current"),
                                  "threshold": evidence.get("behavioural_threshold")},
            f"Behavioural score {behavioural:.2f} (casts no vote)", behavioural)
    vetoes = evidence.get("style_vetoes")
    if isinstance(vetoes, list) and vetoes:
        add("style_veto", {"vetoes": vetoes}, f"Style veto: {str(vetoes[0])[:150]}")

    # Association.
    referrals = [{"kind": x.get("kind"), "with": _addr(x.get("with"))} for x in rows_of("referral_links")
                 if _addr(x.get("with")) in cluster]
    if referrals:
        add("referral_with_cluster", {"links": referrals},
            f"Joined to his wallet {short(referrals[0]['with'])} by a quiet referral code")
    pairs = rows_of("referral_pairs")
    if pairs:
        add("referral_pair", {"pairs": pairs},
            f"Shares a quiet referral code with {len(pairs)} other account(s)")

    # Co-activity.
    comovement = evidence.get("comovement")
    if isinstance(comovement, dict) and comovement.get("verdict"):
        add("comovement", comovement, f"Trade timing against his: {comovement.get('verdict')}")

    # Context.
    overlap = _num(evidence.get("portfolio_overlap"))
    if overlap is not None:
        add("portfolio_overlap", {"score": overlap, "shared_rare_markets": evidence.get("shared_rare_markets")},
            f"Holds his basket (overlap {overlap:.2f}; a copier matches by definition)", overlap)
    if evidence.get("graph_reach_only"):
        add("graph_reach", {"depth": evidence.get("depth"), "chains": evidence.get("chains")},
            "Reached by the transfer graph with no direct flow (reach, not a vote)")
    group = evidence.get("operator_group")
    if isinstance(group, dict):
        add("operator_group", group, f"Member of the sub-account family of {short(group.get('master'))}")
    nonce = evidence.get("hyperevm_nonce")
    if isinstance(nonce, int) and not isinstance(nonce, bool):
        add("hyperevm_nonce", {"nonce": nonce}, f"HyperEVM nonce {nonce}")

    # The operator's own ruling.
    ruling = ruling_of(config, address)
    if ruling and ruling.get("verdict") == "not_him":
        add("operator_not_him", {"note": ruling.get("note"), "date": ruling.get("date")},
            f"Operator ruling: not him ({str(ruling.get('note') or 'no note')[:120]})")
    return items, refutes


def supporting(item: dict) -> bool:
    """Whether an item supports the wallet being his (a positive mid band)."""
    return model.band(item["kind"], item.get("strength"), item.get("facts"))[1] > 0


def classify_row(row: dict, config: dict, tokens: set | None = None) -> dict:
    """What one roster row means for the casebook (spec §6.1).

    status: "invalid" (no address), "target" (him: the reference, never a
    suspect), "rejected" (it would be admitted, but cannot be anyone's trading
    account or is a measured service), "admitted", or "ignored" (nothing in it
    bears on him). A rejected or ignored row may still carry a `reason`, which an
    OPEN case uses to mark itself excluded.
    """
    address = _addr((row or {}).get("wallet"))
    out = {"address": address, "status": "ignored", "why": [], "reason": None,
           "items": [], "refutes": set()}
    if not ADDRESS.fullmatch(address):
        out["status"] = "invalid"
        return out
    if address == _addr(config.get("target_wallet")):
        out["status"] = "target"
        return out
    items, refutes = extract_items(row, config)
    why = []
    tier, peak = row.get("tier"), row.get("peak_tier")
    if tier in ADMITTING_TIERS:
        why.append(f"tier:{tier}")
    elif peak in ADMITTING_TIERS:
        why.append(f"peak_tier:{peak}")
    why += [item["kind"] for item in items if supporting(item)]
    pin = pinned(config).get(address)
    if pin:
        why.append(pin)
    out.update(items=items, refutes=refutes, why=list(dict.fromkeys(why)))
    if pin != "config:known_self":
        reason = not_wallets.classify(address, config=config, token_contracts=tokens)
        service = bool(row.get("is_service")) or tier == "INFRASTRUCTURE"
        if reason or service:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            out["reason"] = (f"not a wallet: {reason}" if reason
                             else f"service: {evidence.get('service_reason') or 'measured service'}")
            out["status"] = "rejected" if out["why"] else "ignored"
            return out
    out["status"] = "admitted" if out["why"] else "ignored"
    return out
