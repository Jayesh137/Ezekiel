"""Item statuses -> family values -> three bands, clusters and the calibration checks
(spec 2026-10-08 §7). Pure.

    log10 posterior odds = prior + for each category: strongest family + half of every other
    family value          = max of its supports, min of its againsts, larger in magnitude if both

now = current and standing items at their low band, central = current/standing at mid
plus lapsed at half mid (the rank key), ceiling = everything not invalidated at high.
None of these is a confidence any detector reads: they order the casebook.
"""

from __future__ import annotations

from src.casebook import model

BANDS = ("now", "central", "ceiling")


def _sig(x: float) -> float:
    return float(f"{x:.6g}")


def item_values(entry: dict) -> tuple[float, float, float]:
    """One item's contribution to (now, central, ceiling) before its family combines."""
    kind = entry.get("kind")
    if kind in model.CONTEXT_KINDS:
        return (0.0, 0.0, 0.0)
    low, mid, high = model.band(kind, entry.get("strength"), entry.get("facts"))
    weights = model.STATUS_WEIGHTS.get(entry.get("status"), (0.0, 0.0, 0.0))
    return (low * weights[0], mid * weights[1], high * weights[2])


def combine(values: list) -> float:
    """A family: the max of its supports, the min of its againsts, or the larger in
    magnitude when both. Never a sum: items in a family share one mechanism."""
    supports = [v for v in values if v > 0]
    against = [v for v in values if v < 0]
    if supports and against:
        return max(max(supports), min(against), key=abs)
    if supports:
        return max(supports)
    if against:
        return min(against)
    return 0.0


def family_values(evidence: dict, excluded: bool = False) -> dict:
    """{family: {now, central, ceiling}} for every family with a non-zero value.

    The study's calibrated tooling verdict supersedes the census-gated program match
    in any band where the study counts: both read the same mechanism (spec §6.2).
    """
    out: dict[str, dict] = {}
    if excluded:
        return out
    entries = [e for e in (evidence or {}).values() if isinstance(e, dict)]
    contributions = [(e, item_values(e)) for e in entries]
    for i, band_name in enumerate(BANDS):
        study_counts = any(e.get("kind") == "study_tooling" and v[i] != 0 for e, v in contributions)
        per: dict[str, list] = {}
        for e, v in contributions:
            kind = e.get("kind")
            spec = model.KINDS.get(kind)
            if spec is None or spec["family"] == "context" or v[i] == 0:
                continue
            if kind == "execution_program" and study_counts:
                continue
            per.setdefault(spec["family"], []).append(v[i])
        for family, values in per.items():
            low, high = model.FAMILY_CLIP.get(family, model.DEFAULT_CLIP)
            value = round(max(low, min(high, combine(values))), 4)
            if value:
                out.setdefault(family, dict.fromkeys(BANDS, 0.0))[band_name] = value
    return out


def totals(families: dict) -> dict:
    out = {}
    for band_name in BANDS:
        by_category: dict[str, list] = {}
        for family, values in families.items():
            value = values.get(band_name, 0.0)
            if value:
                by_category.setdefault(model.FAMILY_CATEGORY.get(family, family), []).append(value)
        total = model.PRIOR_LOG10_ODDS
        for values in by_category.values():
            values.sort(key=abs, reverse=True)
            total += values[0] + model.SECONDARY_WEIGHT * sum(values[1:])
        out[band_name] = round(total, 4)
    return out


def probability(log_odds: float) -> float:
    if log_odds > 300:
        return 1.0
    if log_odds < -300:
        return 0.0
    return 1.0 / (1.0 + 10.0 ** (-log_odds))


def score_evidence(evidence: dict, excluded: bool = False) -> dict:
    families = family_values(evidence, excluded)
    bands = totals(families)
    return {"model": model.MODEL_VERSION, **bands,
            "p_now": _sig(probability(bands["now"])),
            "p_central": _sig(probability(bands["central"])),
            "p_ceiling": _sig(probability(bands["ceiling"])),
            "families": families}


def _members(case: dict) -> set[str]:
    """Addresses this case names as one operator with it: its sub-account family and
    explicit control links (spec §7.5). Referral pairs are related, never merged."""
    links = case.get("links") or {}
    out: set[str] = set()
    group = links.get("operator_group") if isinstance(links.get("operator_group"), dict) else {}
    out |= {str(a).lower() for a in [group.get("master"), *(group.get("subaccounts") or [])] if a}
    if isinstance(links.get("subaccount_of"), str):
        out.add(links["subaccount_of"].lower())
    for link in links.get("explicit_links") or []:
        if isinstance(link, dict) and isinstance(link.get("with"), str):
            out.add(link["with"].lower())
    return out


def clusters(cases: dict, cluster_wallets: set) -> dict[str, str]:
    """address -> cluster id (its smallest member) for suspects joined by control.
    His known wallets are never merged: a link to one of them is evidence about the
    suspect, not membership."""
    excluded = {a.lower() for a in cluster_wallets} | {a for a, c in cases.items() if c.get("known")}
    parent: dict[str, str] = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for address, case in cases.items():
        if address in excluded:
            continue
        for other in _members(case):
            if other != address and other in cases and other not in excluded:
                ra, rb = find(address), find(other)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups: dict[str, list] = {}
    for address in list(parent):
        groups.setdefault(find(address), []).append(address)
    return {a: min(members) for members in groups.values() if len(members) > 1 for a in members}


def score_all(cases: dict, cluster_wallets: set) -> dict[str, dict]:
    """Every case's score. A cluster is one operator: its members share one score over
    the union of their items (per family, the strongest member's value, never a sum)."""
    ids = clusters(cases, cluster_wallets)
    solo = {a: score_evidence(c.get("evidence") or {}, excluded=bool(c.get("excluded")))
            for a, c in cases.items()}
    members: dict[str, list] = {}
    for address, cid in ids.items():
        members.setdefault(cid, []).append(address)
    shared = {}
    for cid, group in members.items():
        merged = {}
        for m in group:
            if cases[m].get("excluded"):
                continue
            for key, entry in (cases[m].get("evidence") or {}).items():
                merged[f"{m}:{key}"] = entry
        shared[cid] = score_evidence(merged)
    out = {}
    for address, case in cases.items():
        cid = ids.get(address)
        base = shared[cid] if cid and not case.get("excluded") else solo[address]
        out[address] = {**base, "cluster": cid, "cluster_size": len(members.get(cid, [])) or 1,
                        "solo_central": solo[address]["central"]}
    return out


def rank_key(case_score: dict, address: str) -> tuple:
    def num(x):
        return x if isinstance(x, (int, float)) and not isinstance(x, bool) else -1e9
    return (-num(case_score.get("central")), -num(case_score.get("now")),
            -num(case_score.get("ceiling")), address)


def calibration(cases: dict, scores: dict) -> dict:
    """The two checks of spec §7.6: reported, never tuned to (rule 4)."""
    unknown = [a for a, c in cases.items() if not c.get("known") and not c.get("excluded")]
    recall = []
    for address, case in sorted(cases.items()):
        if case.get("known") != "config:known_self":
            continue
        key = rank_key(scores[address], address)
        better = sum(1 for u in unknown if rank_key(scores[u], u) < key)
        recall.append({"address": address, "rank_among_unknown": better + 1,
                       "central": scores[address]["central"], "p_central": scores[address]["p_central"]})
    seen, total = set(), 0.0
    for address in unknown:
        cid = scores[address].get("cluster") or address
        if cid in seen:
            continue
        seen.add(cid)
        total += scores[address]["p_central"]
    return {"recall": recall, "unknown_cases": len(unknown), "sum_p_central_unknown": _sig(total),
            "expected_sum": "about 0.5 to 3 if the model is calibrated: his unknown wallets in the casebook"}
