# src/boundary/gaps.py
"""Custody-gap exits, and the route each one could re-emerge through (spec §8).

Measured 2026-10-06: the correlator's $3.39B of "unresolved exits" were mostly
DeFi positions he still held, bridges to himself and $370M of the target's own
withdrawals whose ledger row names no destination. Only money that crossed a
custody gap can re-emerge in a new account, and money that went into an
exchange can only come back out as that exchange's withdrawal.
"""

from __future__ import annotations

from src.boundary.perimeter import low

EXCHANGE_DEPOSIT, PERSON = "exchange_deposit", "person_transfer"
HL_WITHDRAWAL, HL_WITHDRAWAL_UNKNOWN = "hl_withdrawal_outside", "hl_withdrawal_unknown"
EXCHANGE_CLASSES = ("exchange", "busy")
ROUTE_SLACK_S = 86400


def exits(movements, *, index, classify_destination, min_amount: float) -> list[dict]:
    """Movements that crossed a custody gap, each tagged with its `gap`. Pure.

    The movement's own fields (`source` = l1_outbound/hl_withdraw, `ref`,
    `event_ids`, `resolution`) are kept; `classify_destination(address, chain)`
    answers "contract", "busy", "service", "eoa" or "unmeasured" from what the
    project already measured.
    """
    out = []
    for mv in movements or []:
        try:
            amount = float(mv.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if amount < min_amount:
            continue
        base = {**mv, "amount": amount, "ts": int(mv.get("ts") or 0),
                "event_ids": mv.get("event_ids") or [mv.get("id")],
                "wallet": mv.get("source_wallet")}
        if mv.get("source") == "hl_withdraw":
            dest = low(mv.get("destination"))
            if not dest:
                out.append({**base, "gap": HL_WITHDRAWAL_UNKNOWN})
            elif index.get(dest) is None:
                out.append({**base, "gap": HL_WITHDRAWAL, "destination": dest})
            continue
        if mv.get("source") != "l1_outbound" or mv.get("route_resolved"):
            continue
        dest = low(mv.get("destination") or mv.get("immediate_destination"))
        member = index.get(dest)
        if member is not None:
            if member["role"] == "deposit":
                out.append({**base, "gap": EXCHANGE_DEPOSIT, "destination": dest})
            continue
        kind = classify_destination(dest, mv.get("chain"))
        if kind in ("eoa", "unmeasured"):
            out.append({**base, "gap": PERSON, "destination": dest})
        elif kind == "busy":
            out.append({**base, "gap": EXCHANGE_DEPOSIT, "destination": dest})
    return out


def route_consistent(match: dict, record: dict | None) -> tuple[bool, str]:
    """Could this match's money have come back out of the exchange it went into?

    Only an exchange exit is constrained. A route we could not fully read keeps
    the match (`route_unknown`): a gap in what we read is never evidence
    against it.
    """
    if match.get("exit_gap") != EXCHANGE_DEPOSIT:
        return True, "unconstrained"
    if not record or not record.get("complete") or record.get("unreadable"):
        return True, "route_unknown"
    end = int(match.get("deposit_ts") or 0)
    start = end - int(float(match.get("gap_hours") or 0) * 3600) - ROUTE_SLACK_S
    on_route = [s for s in record.get("sources") or [] if s.get("hop") in (1, 2)
                and s.get("class") in EXCHANGE_CLASSES
                and start <= int(s.get("last_ts") or end) <= end]
    if any(s.get("family") for s in on_route):
        return True, "same_exchange"
    if on_route:
        return True, "exchange"
    return False, "no_exchange_source"
