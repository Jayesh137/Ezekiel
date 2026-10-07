"""Stranger and same-operator panels for the tooling tests (spec §8.1). Pure.

Strangers come from the habit census: uniformly sampled large accounts, the
target and the config cluster excluded. Same-operator pairs come from
sub-account families — the HL surface's sub-accounts and the census's
`subAccounts` reads — with two caveats recorded in the spec: a sub-account
shares its master's signer (an upper bound on how alike an operator's accounts
are), and the families measured so far are market makers.
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import tooling


def families(surface: dict | None, census_habits: dict | None, *, exclude=()) -> dict[str, list[str]]:
    """{master: sorted members} for every family of two or more."""
    groups: dict[str, set[str]] = {}
    for sub, detail in ((surface or {}).get("subaccounts") or {}).items():
        master = str((detail or {}).get("master") or "").lower()
        if master and sub:
            groups.setdefault(master, {master}).add(str(sub).lower())
    for master, row in (census_habits or {}).items():
        subs = (row or {}).get("subaccounts") or []
        if subs:
            groups.setdefault(master.lower(), {master.lower()}).update(s.lower() for s in subs)
    blocked = {str(a).lower() for a in exclude}
    out = {}
    for master, members in groups.items():
        kept = sorted(m for m in members if m not in blocked)
        if master not in blocked and len(kept) >= 2:
            out[master] = kept
    return out


def member_pairs(fams: dict, snapshots: dict) -> list[tuple[dict, dict]]:
    pairs = []
    for members in fams.values():
        measured = [snapshots[m] for m in members if snapshots.get(m)]
        pairs.extend((a, b) for i, a in enumerate(measured) for b in measured[i + 1:])
    return pairs


def family_t1(pairs) -> dict:
    """Style agreement, and per-trait disagreement, over pairs where both members
    were measurable. An unmeasurable member is left out, never counted."""
    agree = n = 0
    mismatch = {t: [0, 0] for t in tooling.AGAINST_TRAITS}
    for a, b in pairs:
        if not a.get("style") or not b.get("style"):
            continue
        n += 1
        agree += a["style"] == b["style"]
        for trait in tooling.AGAINST_TRAITS:
            mismatch[trait][1] += 1
            mismatch[trait][0] += a["style"]["flags"][trait] != b["style"]["flags"][trait]
    return {"agree": (agree, n), "mismatch": {t: (v[0], v[1]) for t, v in mismatch.items()}}


def family_t2(pairs) -> list[float]:
    out = []
    for a, b in pairs:
        ha, hb = a.get("cadence") or [], b.get("cadence") or []
        if sum(ha) >= tooling.MIN_GAPS and sum(hb) >= tooling.MIN_GAPS:
            out.append(tooling.wasserstein(ha, hb))
    return out


def family_t3(pairs) -> list[float]:
    out = []
    for a, b in pairs:
        match = ep.compare(tooling.snapshot_signature(a), tooling.snapshot_signature(b))
        if match["status"] == "measured":
            out.append(match["strength"])
    return out


def strangers(census_habits: dict | None, *, exclude=()) -> list[dict]:
    """Measurable strangers: habit-census rows with at least 100 orders read."""
    blocked = {str(a).lower() for a in exclude}
    return [{**row, "wallet": wallet.lower()} for wallet, row in (census_habits or {}).items()
            if wallet.lower() not in blocked and isinstance(row, dict)
            and (row.get("orders_seen") or 0) >= tooling.MIN_ORDERS]


def stranger_t1(rows: list[dict], his_style: dict | None) -> tuple[int, int]:
    """Strangers sharing his style and flags, among those whose style could be
    decided; a stranger whose style is undecidable is unknown, not a non-match."""
    known = [r for r in rows if r.get("style")]
    return sum(1 for r in known if his_style and r["style"] == his_style), len(known)


def stranger_trait_rates(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    return {t: sum(1 for r in rows
                   if ((r.get("shares") or {}).get(t) or 0) > tooling.DOMINANT_SHARE) / len(rows)
            for t in tooling.AGAINST_TRAITS}


def stranger_t2(rows: list[dict], his_hist: list[int]) -> list[float]:
    """Rhythm distance per stranger; one that runs no programs cannot match (inf)."""
    out = []
    for row in rows:
        hist = row.get("cadence") or []
        distance = tooling.wasserstein(hist, his_hist) if sum(hist) >= tooling.MIN_GAPS else None
        out.append(distance if distance is not None else float("inf"))
    return out


def stranger_t3(rows: list[dict], his_sig: dict) -> list[float]:
    """Clip strength per stranger; one with nothing to compare cannot match (-inf)."""
    out = []
    for row in rows:
        match = ep.compare(his_sig or {}, tooling.snapshot_signature(row))
        out.append(match["strength"] if match["status"] == "measured" else float("-inf"))
    return out
