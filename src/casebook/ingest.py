"""One roster reading -> the casebook (spec 2026-10-08 §4, steps 2-3).

The live update and the history backfill both call `apply_roster`, so a case
built from history and a case built live obey exactly the same rules. Mutates
`cases` (address -> case) and `rejected` (address -> why it was set aside).
"""

from __future__ import annotations

from src.casebook import cases as casefile
from src.casebook import extract

MAX_REJECTED = 500


def hyperliquid_knows(case: dict) -> bool:
    """Hyperliquid holds an account at this address: the casebook's own `portfolio`
    read, or the roster's `userRole` read (anything but missing, the roster's own test)."""
    hl = case.get("hl") if isinstance(case.get("hl"), dict) else {}
    roster = hl.get("roster") if isinstance(hl.get("roster"), dict) else {}
    return hl.get("on_hl") is True or (roster.get("role") or "missing") != "missing"


def counterpart_services(cases: dict, activity_table: dict, config: dict,
                         tokens: set | None = None) -> dict[str, str | None]:
    """The verdicts `cases.rejudge` reads, for every address the casebook's items rest
    on: why it cannot be what an item took it for (a measured busy address or contract,
    by the roster's own rule `services_from_activity`, or an address with no key), or
    None when the whole chain measured it and it passes. Unmeasured stays absent: not
    evidence either way. As in the roster, an address Hyperliquid knows is exempt from
    the contract verdict (code on another chain does not unmake an account), never from
    the busy one."""
    from src import not_wallets
    from src.roster import services_from_activity

    wanted = set()
    for case in cases.values():
        for entry in (case.get("evidence") or {}).values():
            other = casefile.counterpart(entry) if isinstance(entry, dict) else None
            if other:
                wanted.add(other)
    if not wanted:
        return {}
    table = activity_table if isinstance(activity_table, dict) else {}
    trading = {a for a, c in cases.items() if isinstance(c, dict) and hyperliquid_knows(c)}
    services = services_from_activity(table, ground_truth=extract.cluster_of(config),
                                      hl_accounts=trading)
    measured = {str(k).split(":", 1)[1].strip().lower() for k, v in table.items()
                if isinstance(v, dict) and ":" in str(k)}
    out: dict[str, str | None] = {}
    for address in wanted:
        keyless = not_wallets.classify(address, config=config, token_contracts=tokens)
        if keyless and keyless != "not an address":
            out[address] = f"not a wallet: {keyless}"
        elif address in services:
            out[address] = services[address]
        elif address in measured:
            out[address] = None
    return out


def rejudge_all(cases: dict, activity_table: dict | None, config: dict, *, at_ms: int,
                origin: str, tokens: set | None = None, blocked=frozenset()) -> list:
    """Re-judge every readable case against today's whole-chain readings, listed in the
    newest roster or not: a measurement can arrive on a day the roster does not move.
    `activity_table` None (absent or unreadable) re-judges nothing, so a failed read
    never hands an invalidated item its vote back."""
    if activity_table is None:
        return []
    verdicts = counterpart_services(cases, activity_table, config, tokens)
    events: list = []
    for address, case in cases.items():
        if address not in blocked:
            events += casefile.rejudge(case, verdicts, at_ms=at_ms, origin=origin)
    return events


def apply_roster(cases: dict, roster: dict, config: dict, *, at_ms: int, origin: str,
                 tokens: set | None = None, refuted_by: dict | None = None,
                 blocked=frozenset(), rejected: dict | None = None) -> dict:
    """Merge one roster reading.

    `refuted_by` maps an evidence kind to the addresses a detector re-checked for
    this reading without finding it (dormancy's checked set): their absence is a
    refutation, not a lapse. `blocked` addresses (case files that could not be
    read) are never touched, so nothing overwrites them. Re-judging against the
    whole-chain readings is `rejudge_all`, run apart because it does not wait for a
    roster.
    """
    rejected = rejected if rejected is not None else {}
    refuted_by = refuted_by or {}
    events, seen = [], set()
    stats = {"rows": 0, "admitted": 0, "opened": 0, "rejected_count": 0}
    rows = roster.get("wallets") if isinstance(roster, dict) else None

    def refutes_for(address, own=()):
        return set(own) | {kind for kind, addresses in refuted_by.items() if address in addresses}

    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        stats["rows"] += 1
        verdict = extract.classify_row(row, config, tokens)
        address = verdict["address"]
        if verdict["status"] in ("invalid", "target") or address in blocked:
            continue
        case = cases.get(address)
        if verdict["reason"]:
            if verdict["status"] == "rejected":
                stats["rejected_count"] += 1
                entry = rejected.setdefault(address, {"address": address,
                                                      "first_seen": casefile.iso(at_ms)})
                entry.update(reason=verdict["reason"], last_seen=casefile.iso(at_ms),
                             why=verdict["why"][:5])
            if case is not None:
                seen.add(address)
                if not case.get("excluded"):
                    case["excluded"] = {"reason": verdict["reason"], "since": casefile.iso(at_ms)}
                    events.append(casefile.event(at_ms, address, "case_excluded", origin,
                                                 reason=verdict["reason"]))
                events += casefile.merge_items(case, [], refutes_for(address), at_ms=at_ms, origin=origin)
                events += casefile.update_roster(case, row, at_ms, origin)
                events += casefile.apply_config(case, config, at_ms=at_ms, origin=origin)
            continue
        if case is None:
            if verdict["status"] != "admitted":
                continue
            case, opened = casefile.new_case(address, at_ms, verdict["why"], origin)
            cases[address] = case
            events += opened
            stats["opened"] += 1
        elif case.get("excluded"):
            case["excluded"] = None
            events.append(casefile.event(at_ms, address, "case_readmitted", origin))
        if verdict["status"] == "admitted":
            stats["admitted"] += 1
        seen.add(address)
        events += casefile.merge_items(case, verdict["items"], refutes_for(address, verdict["refutes"]),
                                       at_ms=at_ms, origin=origin)
        events += casefile.update_roster(case, row, at_ms, origin)
        events += casefile.apply_config(case, config, at_ms=at_ms, origin=origin)

    for address, label in extract.pinned(config).items():
        if address in cases or address in blocked:
            continue
        case, opened = casefile.new_case(address, at_ms, [label], origin)
        cases[address] = case
        events += opened
        stats["opened"] += 1
        seen.add(address)
        events += casefile.apply_config(case, config, at_ms=at_ms, origin=origin)

    for address, case in cases.items():
        if address in seen or address in blocked:
            continue
        events += casefile.merge_items(case, [], refutes_for(address), at_ms=at_ms, origin=origin)
        events += casefile.apply_config(case, config, at_ms=at_ms, origin=origin)

    if len(rejected) > MAX_REJECTED:
        keep = sorted(rejected.values(), key=lambda r: (r.get("last_seen") or "", r["address"]),
                      reverse=True)[:MAX_REJECTED]
        rejected.clear()
        rejected.update({r["address"]: r for r in keep})
    return {"events": events, "rejected": rejected, **stats}
