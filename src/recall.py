"""Recall measurement: would the pipeline catch HIM if he migrated?

The project has many detectors but has never demonstrated it FINDS a hidden
wallet — every check is unit-level. This measures the one that matters most: if he
moves to a fresh wallet and keeps running his execution program, does the
execution-program vector recognise the new wallet as him?

Method (a pseudo-migration on his own history): split his fills at a cutoff, build
his signature from the pre-cutoff half (the reference we would already hold), and
compare the post-cutoff half to it as if it were a fresh wallet. `recognised` is a
strong clip-table match; `caught` additionally requires the census to have measured
the match rare, because without that the vector casts no vote (rule 4). A missing
or too-thin post-cutoff half is `insufficient_data`, never a failure (rules 5, 6).

Pure functions; the script reads real fills and the census and writes the report.
"""

from src import execution_program as ep


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def split_fills(fills: list[dict], cutoff_ms: int) -> tuple[list[dict], list[dict]]:
    before, after = [], []
    for row in fills or []:
        ts = _num(row.get("time"))
        if ts is None:
            continue
        (before if ts <= cutoff_ms else after).append(row)
    return before, after


def self_migration_recall(fills: list[dict], cutoff_ms: int, census: dict | None,
                          orders_before: list[dict] | None = None) -> dict:
    """Compare his post-cutoff trading to his pre-cutoff signature."""
    before, after = split_fills(fills, cutoff_ms)
    reference = ep.signature(before, orders_before)
    migrated = ep.signature(after)
    match = ep.compare(reference, migrated)
    result = {
        "cutoff_ms": cutoff_ms,
        "reference_clip_count": len(reference.get("clip_table") or {}),
        "migrated_clip_count": len(migrated.get("clip_table") or {}),
        "before_fills": len(before), "after_fills": len(after),
        "status": match["status"],
        "clip_match_ratio": match.get("clip_match_ratio"),
        "clips_matched": match.get("clips_matched"),
        "clips_compared": match.get("clips_compared"),
        "notional_structure_rho": match.get("notional_structure_rho"),
        "notional_coins_compared": match.get("notional_coins_compared"),
        "recognised": False, "recognised_by": None, "would_vote": False, "caught": False,
    }
    if match["status"] != "measured":
        return result
    # Recognised by EITHER path: his exact clip sizes, or (rescale-robust) his
    # per-coin rank structure surviving even when the exact sizes drifted.
    exact = (match.get("clip_match_ratio") or 0) >= 0.5 and (match.get("clips_matched") or 0) >= 2
    structural = (match.get("notional_structure_rho") or 0) >= 0.8 and (match.get("notional_coins_compared") or 0) >= 3
    result["recognised"] = bool(exact or structural)
    result["recognised_by"] = "exact_clips" if exact else "rank_structure" if structural else None
    result["would_vote"] = ep.is_discriminating(match, census)
    result["caught"] = result["recognised"] and result["would_vote"]
    return result


def sweep(fills: list[dict], cutoffs: list[int], census: dict | None) -> dict:
    """Recall at several cutoffs, so one unlucky split does not decide the verdict."""
    runs = [self_migration_recall(fills, c, census) for c in cutoffs]
    measured = [r for r in runs if r["status"] == "measured"]
    return {
        "runs": runs,
        "measured_cutoffs": len(measured),
        "recognised_rate": (sum(r["recognised"] for r in measured) / len(measured)) if measured else None,
        "caught_rate": (sum(r["caught"] for r in measured) / len(measured)) if measured else None,
        "census_present": census is not None,
    }
