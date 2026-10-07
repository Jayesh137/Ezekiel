"""The study's tests, rows and dossiers, assembled from in-memory inputs. Pure.

His reference is built from his own daily records by the same code as every
candidate's (spec §6.3). His months against the rest of his history are the
same-operator yardstick in time; the habit census gives the strangers; the
sub-account families give same-operator pairs (spec §8.1).
"""

from __future__ import annotations

from src import execution_program as ep
from src.study import calibration, panels, records, tooling, verdict

REFERENCE_DAYS = 90
SCHEMA = "study/1"


def window(days: dict, last_day: str, n_days: int) -> list[dict]:
    first = records.day_of(records.day_start_ms(last_day) - (n_days - 1) * records.DAY_MS)
    return [days[d] for d in sorted(days) if first <= d <= last_day]


def his_reference(his_days: dict) -> dict:
    """His recent habits and rhythm (90 days, widened until 200 in-run gaps) and his
    whole-history clip signature."""
    if not his_days:
        return {}
    last = max(his_days)
    span = REFERENCE_DAYS
    recent = tooling.summarise(window(his_days, last, span))
    while sum(recent["cadence"]) < tooling.MIN_GAPS and span < 3_650:
        span *= 2
        recent = tooling.summarise(window(his_days, last, span))
    full = tooling.summarise([his_days[d] for d in sorted(his_days)])
    recent_profile = tooling.summary_profile(recent)
    full_profile = tooling.summary_profile(full)
    # A style is published only from a recent window of at least MIN_ORDERS orders (as T1
    # requires of his side): fewer would pin a style on a handful of orders.
    measured = bool(recent_profile) and recent_profile["orders_seen"] >= tooling.MIN_ORDERS
    return {"last_day": last, "span_days": span, "recent_profile": recent_profile,
            "full_profile": full_profile,
            "style": tooling.style(recent_profile) if measured else None,
            "cadence": recent["cadence"],
            "signature": tooling.clip_signature(full, full_profile)}


def self_splits(his_days: dict, ref: dict) -> dict:
    """Each of his months against the rest of him: T1 agreement, T2 distances (against
    his recent window without that month), T3 strengths (against all his other months)."""
    out = {"t1": (0, 0), "t2": [], "t3": []}
    if not his_days or not ref:
        return out
    agree = total = 0
    ordered = [his_days[d] for d in sorted(his_days)]
    recent = window(his_days, ref["last_day"], ref.get("span_days", REFERENCE_DAYS))
    for month in sorted({d[:7] for d in his_days}):
        mine = tooling.summarise([r for r in ordered if r["day"].startswith(month)])
        rest_recent = tooling.summarise([r for r in recent if not r["day"].startswith(month)])
        rest_all = tooling.summarise([r for r in ordered if not r["day"].startswith(month)])
        mp, rp = tooling.summary_profile(mine), tooling.summary_profile(rest_recent)
        # A style that cannot be decided (tooling.style is None) is unknown, not a style:
        # the window is no window, never an agreement and never a disagreement.
        mine_style = tooling.style(mp) if mp and mp["orders_seen"] >= tooling.MIN_ORDERS else None
        rest_style = tooling.style(rp) if rp and rp["orders_seen"] >= tooling.MIN_ORDERS else None
        if mine_style is not None and rest_style is not None:
            total += 1
            agree += mine_style == rest_style
        if (sum(mine["cadence"]) >= tooling.MIN_GAPS
                and sum(rest_recent["cadence"]) >= tooling.MIN_GAPS):
            out["t2"].append(tooling.wasserstein(mine["cadence"], rest_recent["cadence"]))
        match = ep.compare(tooling.clip_signature(rest_all, tooling.summary_profile(rest_all)),
                           tooling.clip_signature(mine, mp))
        if match["status"] == "measured":
            out["t3"].append(match["strength"])
    out["t1"] = (agree, total)
    return out


def panel_context(ref: dict, splits: dict, stranger_rows: list, family_pairs: list) -> dict:
    """The panels every judgement is made against, and a `status` that counts exactly what
    each judgement used (T1 and `against` judge on the strangers whose style is decidable;
    T2 and T3 have their own strangers, family pairs and self windows)."""
    family = panels.family_t1(family_pairs)
    stranger_k, stranger_n = panels.stranger_t1(stranger_rows, ref.get("style"))
    t2_strangers = panels.stranger_t2(stranger_rows, ref.get("cadence") or [])
    t2_family = panels.family_t2(family_pairs)
    t3_strangers = panels.stranger_t3(stranger_rows, ref.get("signature") or {})
    t3_family = panels.family_t3(family_pairs)
    return {
        "t1": {"stranger": (stranger_k, stranger_n),
               "same_op": {"family": family["agree"], "self": splits["t1"]},
               "mismatch": family["mismatch"],
               "trait_rates": panels.stranger_trait_rates(stranger_rows)},
        "t2": {"strangers": t2_strangers,
               "same_op": {"family": t2_family, "self": splits["t2"]}},
        "t3": {"strangers": t3_strangers,
               "same_op": {"family": t3_family, "self": splits["t3"]}},
        "status": {
            "strangers": stranger_n, "measurable_strangers": len(stranger_rows),
            "family_pairs": family["agree"][1], "self_windows": splits["t1"][1],
            "by_test": {
                "T1": {"strangers": stranger_n, "family_pairs": family["agree"][1],
                       "self_windows": splits["t1"][1]},
                "T2": {"strangers": len(t2_strangers), "family_pairs": len(t2_family),
                       "self_windows": len(splits["t2"])},
                "T3": {"strangers": len(t3_strangers), "family_pairs": len(t3_family),
                       "self_windows": len(splits["t3"])}}},
    }


def tooling_tests(days: list[dict], ref: dict, ctx: dict) -> dict:
    summary = tooling.summarise(days)
    prof = tooling.summary_profile(summary)
    t1 = tooling.t1_style(prof, ref.get("recent_profile"), ref.get("full_profile"))
    stranger_k, stranger_n = ctx["t1"]["stranger"]
    t1["judgement"] = calibration.judge_binary(
        t1["statistic"] if t1["status"] == "measured" else None,
        stranger_k=stranger_k, stranger_n=stranger_n, same_op=ctx["t1"]["same_op"])
    t1["against"] = calibration.judge_against(
        t1["detail"].get("against_traits", []), family_mismatch=ctx["t1"]["mismatch"],
        stranger_trait_rate=ctx["t1"]["trait_rates"], stranger_n=stranger_n)
    t2 = tooling.t2_rhythm(summary["cadence"], ref.get("cadence") or [])
    t2["judgement"] = calibration.judge_continuous(
        t2["statistic"], strangers=ctx["t2"]["strangers"], same_op=ctx["t2"]["same_op"],
        higher_is_better=False)
    t3 = tooling.t3_clips(tooling.clip_signature(summary, prof), ref.get("signature") or {})
    t3["judgement"] = calibration.judge_continuous(
        t3["statistic"], strangers=ctx["t3"]["strangers"], same_op=ctx["t3"]["same_op"],
        higher_is_better=True)
    return {"T1": t1, "T2": t2, "T3": t3,
            "summary": {"days": summary["days"], "covered_days": summary["covered_days"],
                        "orders": summary["orders"], "cadence": summary["cadence"]}}


def key_numbers(tests: dict) -> dict:
    detail = tests["T1"].get("detail") or {}
    shares = detail.get("shares") or {}
    return {"style": (detail.get("candidate") or {}).get("style"),
            "client_ids": shares.get("client_ids"), "maker": shares.get("maker"),
            "rhythm_s": tests["T2"].get("statistic"), "clip_strength": tests["T3"].get("statistic")}


def study_row(member: dict, tests: dict, account_value, last_read_ms) -> dict:
    families = {"tooling": {**verdict.family_verdict(tests, verdict.TOOLING),
                            "key": key_numbers(tests)}}
    return {"wallet": member["wallet"], "source": member["source"],
            "studied_since_ms": member["since_ms"], "account_value": account_value,
            "coverage_days": tests["summary"]["covered_days"], "orders": tests["summary"]["orders"],
            "last_read_ms": last_read_ms, "families": families, "rank": verdict.rank(families)}


def dossier(member: dict, tests: dict, ref: dict) -> dict:
    """Everything the Study page draws for one wallet. No timestamp of its own, so an
    unchanged dossier is not rewritten, and none of his series either: his are rebuilt
    every run, so they live once in `latest_doc`'s reference (`ref` is taken only so the
    caller's signature stays the same)."""
    detail = tests["T1"].get("detail") or {}
    return {"schema": SCHEMA, "wallet": member["wallet"], "source": member["source"],
            "tests": {name: tests[name] for name in verdict.TOOLING},
            "series": {"cadence": tests["summary"]["cadence"], "shares": detail.get("shares")}}


def _his_shares(profile: dict | None) -> dict | None:
    """His recent shares for every key the habit table shows, rounded as T1 rounds them;
    unknown stays None."""
    if not profile:
        return None
    return {k: (round(profile[k], 4) if profile.get(k) is not None else None)
            for k in tooling.SHARE_KEYS}


def latest_doc(computed_at: str, target: str, ref: dict, ctx_status: dict, rows: list,
               collection: dict) -> dict:
    return {"computed_at": computed_at, "schema": SCHEMA, "target": target,
            "reference": {"style": ref.get("style"), "last_day": ref.get("last_day"),
                          "cadence_gaps": sum(ref.get("cadence") or []),
                          "cadence": ref.get("cadence"),
                          "shares": _his_shares(ref.get("recent_profile"))},
            "panels": {**ctx_status,
                       "bars": {"strangers": calibration.MIN_STRANGERS,
                                "family_pairs": calibration.MIN_SAME_OP["family"],
                                "self_windows": calibration.MIN_SAME_OP["self"]}},
            "studied": len(rows), "read": len(collection.get("read") or []),
            "unreadable": collection.get("unreadable") or [],
            "stopped": bool(collection.get("stopped")), "budget": collection.get("budget"),
            "wallets": sorted(rows, key=lambda r: (-r["rank"], -(r.get("account_value") or 0),
                                                   r["wallet"]))}
