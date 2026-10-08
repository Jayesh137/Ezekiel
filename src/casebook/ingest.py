"""One roster reading -> the casebook (spec 2026-10-08 §4, steps 2-3).

The live update and the history backfill both call `apply_roster`, so a case
built from history and a case built live obey exactly the same rules. Mutates
`cases` (address -> case) and `rejected` (address -> why it was set aside).
"""

from __future__ import annotations

from src.casebook import cases as casefile
from src.casebook import extract

MAX_REJECTED = 500


def apply_roster(cases: dict, roster: dict, config: dict, *, at_ms: int, origin: str,
                 tokens: set | None = None, refuted_by: dict | None = None,
                 blocked=frozenset(), rejected: dict | None = None) -> dict:
    """Merge one roster reading.

    `refuted_by` maps an evidence kind to the addresses a detector re-checked for
    this reading without finding it (dormancy's checked set): their absence is a
    refutation, not a lapse. `blocked` addresses (case files that could not be
    read) are never touched, so nothing overwrites them.
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
