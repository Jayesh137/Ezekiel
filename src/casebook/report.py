"""The index (spec 2026-10-08 §11, §13): one compact, ranked row per case, plus the
model, the calibration checks and what the run read. Pure.

Order: his known wallets first (the recall check, shown, never ranked), then every
other case ranked by its central estimate, then excluded cases. Generated text is ASCII.
"""

from __future__ import annotations

from src.casebook import model, score

INDEX_SCHEMA = "casebook-index/1"
MAX_REJECTED_SHOWN = 500
STATUS_ORDER = ("current", "standing", "lapsed", "refuted", "historical", "invalidated")
FOLLOWABLE_MIN_USD = 10_000.0


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def money(x) -> str:
    if not _num(x):
        return "an unknown amount"
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:.1f}B"
    if a >= 1e6:
        return f"${x / 1e6:.1f}M"
    if a >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def followable(case: dict) -> bool:
    """Could the owner follow it on Hyperliquid today: an account holding money that
    traded in the last 30 days."""
    hl = case.get("hl") or {}
    value, month = hl.get("total_value"), hl.get("month_volume")
    return (hl.get("on_hl") is True and _num(value) and value >= FOLLOWABLE_MIN_USD
            and _num(month) and month > 0)


def hl_text(case: dict) -> str:
    hl = case.get("hl") or {}
    if not hl.get("last_ok_at"):
        return "not read on Hyperliquid yet"
    if hl.get("on_hl") is False:
        return "no Hyperliquid account"
    month = hl.get("month_volume")
    if _num(month) and month > 0:
        traded = f", {money(month)} traded in 30 days"
    elif _num(month):
        traded = ", no trades in 30 days"
    else:
        traded = ""
    return f"{money(hl.get('total_value'))} on Hyperliquid{traded}"


def _scored(case: dict) -> list[dict]:
    return [e for e in (case.get("evidence") or {}).values()
            if isinstance(e, dict) and e.get("kind") not in model.CONTEXT_KINDS]


def best_item(case: dict, bands: tuple = (1, 2)) -> dict | None:
    """The case's own strongest supporting item: what counts now (central, band 1),
    else what it once had (ceiling, band 2)."""
    items = _scored(case)
    for band_index in bands:
        ranked = [(score.item_values(e)[band_index], e) for e in items]
        ranked = [(v, e) for v, e in ranked if v > 0]
        if ranked:
            return max(ranked, key=lambda pair: pair[0])[1]
    return None


def _why(item: dict) -> str:
    why = item.get("summary") or model.KINDS.get(item.get("kind"), {}).get("label") or item.get("kind")
    if item.get("status") not in ("current", "standing"):
        why = f"{why} ({item.get('status')})"
    return why


def headline(case: dict, cluster_note: tuple | None = None) -> str:
    """Why it ranks where it does, in one line, and where it stands on Hyperliquid:
    its own current evidence first, then the current evidence of its operator
    cluster (whose member it names), then its own history, then the roster's words."""
    current = best_item(case, (1,))
    note_address, note = cluster_note or (None, None)
    if current is not None:
        why = _why(current)
    elif note and note_address != case.get("address"):
        why = note
    else:
        past = best_item(case, (2,))
        if past is not None:
            why = _why(past)
        else:
            reasons = (case.get("roster") or {}).get("reasons") or []
            why = reasons[0] if reasons else "Opened by " + ", ".join(case.get("opened_by") or ["unknown"])
    return f"{why} - {hl_text(case)}"


def cluster_notes(cases: dict, scores: dict) -> dict[str, tuple]:
    """cluster id -> (member, "One operator with <member> (<n> accounts): <its evidence>"),
    from the member whose own current evidence is strongest, else its strongest history
    (spec §7.5)."""
    members: dict[str, list] = {}
    for address, s in scores.items():
        if s.get("cluster") and address in cases:
            members.setdefault(s["cluster"], []).append(address)
    notes = {}
    for cid, group in members.items():
        scored = []
        for address in sorted(group):
            item = best_item(cases[address])
            if item is not None:
                values = score.item_values(item)
                scored.append(((values[1] > 0, values[1] or values[2]), address, item))
        if scored:
            _, address, item = max(scored, key=lambda t: t[0])
            notes[cid] = (address, f"One operator with {address[:10]}... ({len(group)} accounts): {_why(item)}")
    return notes


def next_checks(case: dict) -> list[str]:
    """The cheapest reads that could move this case, most useful first."""
    checks = []
    hl = case.get("hl") or {}
    evidence = case.get("evidence") or {}
    if not hl.get("last_ok_at"):
        checks.append("Not read on Hyperliquid yet: the sleeper watch reads it on a coming run")
    elif hl.get("on_hl") is False:
        checks.append("No Hyperliquid account yet: the sleeper watch alerts if it opens one")
    elif _num(hl.get("month_volume")) and hl["month_volume"] > 0 \
            and not ({"study_tooling", "study_context"} & set(evidence)):
        checks.append("Trades on Hyperliquid but was never studied: add it to config.study_wallets "
                      "to read its order habits")
    items = _scored(case)
    if items and all(e.get("status") not in ("current", "standing") for e in items):
        checks.append("Every piece of its evidence has lapsed or is history: the case rests on its "
                      "record (python scripts/casebook.py show <address>)")
    return checks


def family_cells(case_score: dict, case: dict) -> dict:
    """{family: [now, central, ceiling, state]}; state is the best status among this
    case's own items in the family, or "cluster" when only a cluster member carries it."""
    states: dict[str, str] = {}
    for e in _scored(case):
        spec = model.KINDS.get(e.get("kind"))
        if not spec:
            continue
        status = e.get("status") if e.get("status") in STATUS_ORDER else "invalidated"
        held = states.get(spec["family"])
        if held is None or STATUS_ORDER.index(status) < STATUS_ORDER.index(held):
            states[spec["family"]] = status
    return {family: [values.get("now", 0.0), values.get("central", 0.0), values.get("ceiling", 0.0),
                     states.get(family, "cluster")]
            for family, values in (case_score.get("families") or {}).items()}


def index_row(case: dict, case_score: dict, rank: int | None,
              cluster_note: str | None = None) -> dict:
    statuses: dict[str, int] = {}
    for e in _scored(case):
        status = e.get("status") or "current"
        statuses[status] = statuses.get(status, 0) + 1
    hl = case.get("hl") or {}
    roster = case.get("roster") or {}
    return {
        "rank": rank, "address": case["address"], "known": case.get("known"), "pinned": case.get("pinned"),
        "ruling": (case.get("ruling") or {}).get("verdict"),
        "excluded": (case.get("excluded") or {}).get("reason"),
        "now": case_score["now"], "central": case_score["central"], "ceiling": case_score["ceiling"],
        "p": case_score["p_central"], "p_now": case_score["p_now"], "p_ceiling": case_score["p_ceiling"],
        "solo_central": case_score.get("solo_central"), "cluster": case_score.get("cluster"),
        "cluster_size": case_score.get("cluster_size") or 1,
        "families": family_cells(case_score, case),
        "headline": headline(case, cluster_note),
        "tier": roster.get("tier"), "peak_tier": roster.get("peak_tier"),
        "opened_at": case.get("opened_at"), "last_change": case.get("last_change"),
        "hl": {"on_hl": hl.get("on_hl"), "role": (hl.get("roster") or {}).get("role"),
               "value": hl.get("total_value"), "month_volume": hl.get("month_volume"),
               "day_volume": hl.get("day_volume"), "birth_ms": hl.get("birth_ms"),
               "probed_at": hl.get("probed_at"), "probe_ok": hl.get("probe_ok")},
        "followable": followable(case),
        "statuses": statuses,
        "lapsed_only": bool(statuses) and not (statuses.get("current") or statuses.get("standing")),
    }


def build_index(cases: dict, scores: dict, *, rejected: dict, run: dict, target: dict,
                calibration: dict, now_iso: str) -> dict:
    def ordered(keep):
        return sorted((a for a, c in cases.items() if keep(c)), key=lambda a: score.rank_key(scores[a], a))

    known = ordered(lambda c: c.get("known") and not c.get("excluded"))
    unknown = ordered(lambda c: not c.get("known") and not c.get("excluded"))
    excluded = ordered(lambda c: bool(c.get("excluded")))
    notes = cluster_notes(cases, scores)

    def row(a, rank):
        return index_row(cases[a], scores[a], rank, notes.get(scores[a].get("cluster")))

    rows = [row(a, None) for a in known]
    rows += [row(a, i + 1) for i, a in enumerate(unknown)]
    rows += [row(a, None) for a in excluded]
    rejected_rows = sorted((r for r in (rejected or {}).values() if isinstance(r, dict)),
                           key=lambda r: (r.get("last_seen") or "", r.get("address") or ""),
                           reverse=True)[:MAX_REJECTED_SHOWN]
    return {
        "schema": INDEX_SCHEMA, "computed_at": now_iso, "model": model.describe(),
        "counts": {"cases": len(cases), "known": len(known), "unknown": len(unknown),
                   "excluded": len(excluded),
                   "on_hl": sum(1 for c in cases.values() if (c.get("hl") or {}).get("on_hl") is True),
                   "followable": sum(1 for a in unknown if followable(cases[a])),
                   "with_current_evidence": sum(1 for r in rows if r["statuses"].get("current")
                                                or r["statuses"].get("standing")),
                   "lapsed_only": sum(1 for r in rows if r["lapsed_only"]),
                   "rejected": len(rejected or {})},
        "calibration": calibration, "target": target, "run": run,
        "cases": rows, "rejected": rejected_rows,
    }
