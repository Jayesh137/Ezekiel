#!/usr/bin/env python3
"""Keep every suspect forever, ranked by how likely each is his (spec 2026-10-08).

The ONLY writer of data/casebook/ (trace.yml, after the roster). Each run merges the
newest roster into the case files, re-reads due suspects on Hyperliquid (`portfolio`,
budgeted), scores every case, writes the index, and alerts when a suspect wakes up.

    python scripts/update_casebook.py                   # the workflow step
    python scripts/update_casebook.py --dry-run DIR     # real inputs; the casebook goes to
                                                        # DIR/casebook; no alert is sent
    python scripts/update_casebook.py --no-probe        # merge and score only

Exits 1 when the roster, a case file or the state cannot be read: each needs a human,
and a red step is how this project says so. Everything readable is still written; an
unreadable state.json is set aside whole (state.unreadable-<time>.json), because the
rejections it holds exist nowhere else.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils
from src.casebook import cases as casefile
from src.casebook import extract, ingest, report, score, store, vitals
from src.not_wallets import token_registry_addresses

PROBE_SECONDS = 150
PROBE_WEIGHT_PER_MINUTE = 600
TOP_PROBE = 25
HOUR_MS = 3_600_000
FAILED_RETRY_MS = HOUR_MS
TOP_AGE_MS = 12 * HOUR_MS
ON_HL_AGE_MS = 72 * HOUR_MS
OFF_HL_AGE_MS = 168 * HOUR_MS
DORMANCY_FRESH_MS = 24 * HOUR_MS
ALERT_TOP = 25
ALERT_MIN_CENTRAL = -2.0
ALERT_KINDS = ("hl_opened", "hl_woke", "hl_grew")


def read_json(path: Path):
    """(doc, None), or (None, why) when the file is absent or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]


def computed_ms(doc) -> int | None:
    return casefile.parse_ms(doc.get("computed_at")) if isinstance(doc, dict) else None


def dormancy_refutes(dormancy, at_ms: int) -> dict:
    """Wallets the dormancy detector re-checked within a day of this roster and found
    no handoff for: a refutation, not a lapse (spec §6.3)."""
    when = computed_ms(dormancy)
    if when is None or abs(at_ms - when) > DORMANCY_FRESH_MS:
        return {}
    checked = (dormancy.get("collection") or {}).get("checked") or []
    handoffs = dormancy.get("handoffs") or {}
    return {"dormancy_handoff": {str(a).lower() for a in checked} - {str(a).lower() for a in handoffs}}


def target_state(dormancy, config: dict) -> dict:
    doc = dormancy if isinstance(dormancy, dict) else {}
    state = doc.get("dormancy") if isinstance(doc.get("dormancy"), dict) else {}
    gaps = doc.get("anomalous_gaps") if isinstance(doc.get("anomalous_gaps"), list) else []
    return {"address": (config.get("target_wallet") or "").lower(), "computed_at": doc.get("computed_at"),
            "silent_days": state.get("silent_days"), "unusual": state.get("unusual"),
            "unprecedented": state.get("unprecedented"), "anomalous_gaps": gaps[:50]}


def _age(case: dict, now_ms: int) -> int | None:
    when = casefile.parse_ms((case.get("hl") or {}).get("probed_at"))
    return None if when is None else now_ms - when


def probe_order(cases: dict, scores: dict, now_ms: int) -> list[str]:
    """Due cases, most urgent first (spec §8): the top 25 in rank order, then never
    read, failed reads, Hyperliquid accounts, the rest. A case that is not due is not
    read. The top 25 lead because a backlog of never-read cases (about 670 when the
    casebook went live) would otherwise hold their twelve-hourly read for a day or more."""
    live = [a for a, c in cases.items() if not c.get("excluded")]
    unknown = sorted((a for a in live if not cases[a].get("known")),
                     key=lambda a: score.rank_key(scores[a], a))
    top = set(unknown[:TOP_PROBE])
    buckets: list[list[str]] = [[], [], [], [], []]
    for address in unknown + sorted(a for a in live if cases[a].get("known")):
        hl = cases[address].get("hl") or {}
        age = _age(cases[address], now_ms)
        failed = hl.get("probe_ok") is False and age is not None and age >= FAILED_RETRY_MS
        if address in top:
            if age is None or failed or age >= TOP_AGE_MS:
                buckets[0].append(address)
        elif age is None:
            buckets[1].append(address)
        elif failed:
            buckets[2].append(address)
        elif hl.get("on_hl") is True and age >= ON_HL_AGE_MS:
            buckets[3].append(address)
        elif hl.get("on_hl") is not True and age >= OFF_HL_AGE_MS:
            buckets[4].append(address)
    return [a for bucket in buckets for a in bucket]


def probe_cases(cases: dict, scores: dict, *, now_ms: int, fetch, budget_seconds: float,
                blocked) -> tuple[list, dict]:
    from src.hl_budget import ReadBudget

    order = [a for a in probe_order(cases, scores, now_ms) if a not in blocked]
    stats = {"due": len(order), "attempted": 0, "ok": 0, "failed": 0, "stopped": None}
    events: list = []
    with ReadBudget(seconds=budget_seconds, weight_per_minute=PROBE_WEIGHT_PER_MINUTE) as budget:
        for address in order:
            if not budget.can_continue():
                stats["stopped"] = budget.stopped_reason
                break
            result = fetch({"type": "portfolio", "user": address})
            ok = isinstance(result, dict) and result.get("ok") is True
            reading = vitals.parse_portfolio(result.get("data")) if ok else None
            if reading is None and budget.stopped_reason:
                stats["stopped"] = budget.stopped_reason       # a refusal, not a failed read
                break
            stats["attempted"] += 1
            if reading is None:
                stats["failed"] += 1
                error = result.get("error") if isinstance(result, dict) else None
                vitals.apply_probe(cases[address], None, now_ms=now_ms,
                                   error=error or "not a portfolio answer")
                continue
            stats["ok"] += 1
            events.extend(casefile.event(now_ms, address, found["kind"], "live", **found["detail"])
                          for found in vitals.apply_probe(cases[address], reading, now_ms=now_ms))
        stats["budget"] = budget.report()
    return events, stats


def wake_alerts(events: list, index: dict, dormancy, send) -> int:
    """Alert on a suspect waking: top 25 or about 1% and up, never a known or ruled-out
    wallet. CRITICAL while he is in an unusual silence (spec §8)."""
    rows = {r.get("address"): r for r in index.get("cases") or []}
    state = dormancy.get("dormancy") if isinstance(dormancy, dict) else None
    silence = bool((state or {}).get("unusual"))
    sent = 0
    for e in events:
        if e.get("kind") not in ALERT_KINDS or e.get("origin") != "live":
            continue
        row = rows.get(e.get("address"))
        if not row or row.get("known") or row.get("excluded") or row.get("ruling") == "not_him":
            continue
        rank, central = row.get("rank"), row.get("central")
        top = isinstance(rank, int) and rank <= ALERT_TOP
        likely = isinstance(central, (int, float)) and central >= ALERT_MIN_CENTRAL
        if (top or likely) and send(e["address"], e["kind"], e.get("detail") or {}, row, silence):
            sent += 1
    return sent


def run(config: dict, *, data_dir: Path, out_dir: Path, probe: bool = True, fetch=None,
        now_ms: int | None = None, alerts_on: bool = True, send=None,
        budget_seconds: float = PROBE_SECONDS) -> dict:
    now_ms = now_ms or utils.now_ms()
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    fetch = fetch or utils.hl_read
    roster, roster_error = read_json(data_dir / "roster" / "latest.json")
    dormancy, _ = read_json(data_dir / "dormancy" / "latest.json")
    state, state_problem = store.load_state(out_dir)
    cases, unreadable = store.load_cases(out_dir)
    blocked = {u["address"] for u in unreadable}
    rejected = {r["address"]: r for r in state.get("rejected") or []
                if isinstance(r, dict) and r.get("address")}
    out = {"at": casefile.iso(now_ms), "unreadable_cases": unreadable,
           "exit_code": 1 if unreadable else 0, "probes": {}}
    if state_problem:
        kept = store.set_aside_state(out_dir, now_ms=now_ms)
        out.update(state=f"unreadable ({state_problem}): set aside as {kept.name}; a fresh one "
                         "is written and the roster re-read", exit_code=1)
    events: list = []
    tokens = token_registry_addresses(data_dir / "labels" / "token_contracts.json")
    at_ms, consumed = computed_ms(roster), state.get("roster_computed_at_ms")
    if roster is None:
        out.update(roster_status=f"unreadable: {roster_error}", exit_code=1)
    elif at_ms is None:
        out.update(roster_status="unreadable: no computed_at", exit_code=1)
    elif isinstance(consumed, int) and at_ms <= consumed:
        out["roster_status"] = "stale (already consumed)"
    else:
        merged = ingest.apply_roster(
            cases, roster, config, at_ms=at_ms, origin="live", tokens=tokens,
            refuted_by=dormancy_refutes(dormancy, at_ms), blocked=blocked, rejected=rejected)
        events += merged["events"]
        state["roster_computed_at_ms"] = at_ms
        out.update(roster_status="consumed", rows=merged["rows"], opened=merged["opened"],
                   admitted=merged["admitted"], rejected=merged["rejected_count"])
    activity, activity_error = read_json(data_dir / "labels" / "address_activity.json")
    if not isinstance(activity, dict):
        activity = None
        out["activity"] = f"unreadable: {activity_error or 'not a table'}; nothing re-judged"
    events += ingest.rejudge_all(cases, activity, config, at_ms=now_ms, origin="live",
                                 tokens=tokens, blocked=blocked)
    scores = score.score_all(cases, extract.cluster_of(config))
    if probe and cases:
        found, out["probes"] = probe_cases(cases, scores, now_ms=now_ms, fetch=fetch,
                                           budget_seconds=budget_seconds, blocked=blocked)
        events += found
    for address, case in cases.items():
        events += casefile.record_score(case, scores[address], at_ms=now_ms, origin="live")
    casefile.touch(cases, events)
    calibration = score.calibration(cases, scores)
    out["events"] = len(events)
    run_info = {k: v for k, v in out.items() if k not in ("unreadable_cases", "exit_code")}
    run_info["unreadable_cases"] = len(unreadable)
    index = report.build_index(cases, scores, rejected=rejected, run=run_info,
                               target=target_state(dormancy, config), calibration=calibration,
                               now_iso=casefile.iso(now_ms))
    out["written"] = store.write_cases(cases, out_dir, skip=blocked)
    store.append_events(events, out_dir)
    store.write_index(index, out_dir)
    state["rejected"] = sorted(rejected.values(), key=lambda r: r.get("last_seen") or "",
                               reverse=True)[:ingest.MAX_REJECTED]
    state["last_run"] = {**run_info, "written": out["written"]}
    store.write_state(state, out_dir)
    out["alerts"] = 0
    if alerts_on and events:
        if send is None:
            from src.alerts import alert_casebook_wake as send
        out["alerts"] = wake_alerts(events, index, dormancy, send)
    out["counts"], out["calibration"] = index["counts"], calibration
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update the casebook (spec 2026-10-08).")
    parser.add_argument("--dry-run", type=Path, metavar="DIR",
                        help="read the real inputs, write the casebook under DIR/casebook, send no alert")
    parser.add_argument("--no-probe", action="store_true", help="merge and score only")
    parser.add_argument("--budget-seconds", type=float, default=PROBE_SECONDS)
    args = parser.parse_args(argv)
    out = run(utils.load_config(), data_dir=utils.DATA_DIR, out_dir=args.dry_run or utils.DATA_DIR,
              probe=not args.no_probe, alerts_on=args.dry_run is None,
              budget_seconds=args.budget_seconds)
    counts = out.get("counts") or {}
    print(f"[casebook] roster: {out.get('roster_status')}; cases {counts.get('cases')} "
          f"(unknown {counts.get('unknown')}, known {counts.get('known')}, excluded "
          f"{counts.get('excluded')}, on Hyperliquid {counts.get('on_hl')}); opened {out.get('opened', 0)}; "
          f"events {out.get('events')}; files written {out.get('written')}")
    probes = out.get("probes") or {}
    if probes:
        print(f"[casebook] probes: due {probes.get('due')}, ok {probes.get('ok')}, failed "
              f"{probes.get('failed')}" + (f", stopped: {probes['stopped']}" if probes.get("stopped") else ""))
    calibration = out.get("calibration") or {}
    for row in calibration.get("recall") or []:
        print(f"[casebook] recall: known {row['address'][:12]}... ranks {row['rank_among_unknown']} "
              f"among {calibration.get('unknown_cases')} unknown cases on its evidence alone")
    print(f"[casebook] coherence: central probabilities over unknown cases sum to "
          f"{calibration.get('sum_p_central_unknown')} ({calibration.get('expected_sum')})")
    if out.get("activity"):
        print(f"[casebook] activity table {out['activity']}")
    if out.get("state"):
        print(f"[casebook] STATE {out['state']}")
    for bad in out.get("unreadable_cases") or []:
        print(f"[casebook] UNREADABLE case file {bad['address']}: {bad['error']} (left untouched)")
    if out.get("alerts"):
        print(f"[casebook] wake alerts sent: {out['alerts']}")
    return int(out.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
