#!/usr/bin/env python3
"""Keep every identified Hyperliquid candidate under study (spec 2026-10-06).

The ONLY writer of data/study/. A run chooses the study set; reads each wallet's
new fills (and, daily, its orders and ledger) strictly and folds them into daily
records behind a quiet boundary; spends what the budget leaves measuring
sub-account families; builds his own records from data/fills, orders and ledger
with the same code; runs the tooling tests, calibrated on the habit census, the
families and his own months; and writes latest.json, the dossiers and state, then
rolls sealed months. Usage:

    python scripts/run_study.py                       # production (study.yml)
    python scripts/run_study.py --data-dir /tmp/x/data --max-wallets 8   # dry run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils
from src.hl_budget import ReadBudget
from src.study import archive, assemble, collect, panels, records, selection, tooling

FIRST_READ_DAYS = 14
ORDERS_EVERY_MS = 24 * records.HOUR_MS
WINDOW_DAYS = 120
FAMILY_REMEASURE_MS = 30 * records.DAY_MS
FAMILY_MEMBERS_PER_RUN = 8
READ_SECONDS = 900
WEIGHT_PER_MINUTE = 900


def read_json(path: Path, default):
    doc = archive.read_json(path, default)
    return doc if isinstance(doc, type(default)) else default


def detector_wallets(data_dir: Path) -> list[str]:
    """HL accounts other detectors found (spec §5 source 4), strongest first: his
    program's reproductions, then money traced from his world, then births in his
    silences, then large newborns. `selection.by_source` re-sorts them by roster
    evidence with a stable sort, so this order breaks the ties."""
    found: list = []
    census = read_json(data_dir / "execution_program" / "census.json", {})
    found += [h.get("wallet") for h in census.get("hits") or [] if isinstance(h, dict)]
    tape = read_json(data_dir / "tape" / "latest.json", {})
    found += [h.get("wallet") for h in tape.get("program_hits") or [] if isinstance(h, dict)]
    provenance = read_json(data_dir / "provenance" / "latest.json", {})
    found += [f.get("account") for f in provenance.get("findings") or [] if isinstance(f, dict)]
    handoffs = read_json(data_dir / "dormancy" / "latest.json", {}).get("handoffs") or {}
    found += sorted(handoffs, key=lambda a: -float((handoffs[a] or {}).get("score") or 0))
    newborn = read_json(data_dir / "newborn" / "latest.json", {}).get("newborn") or []
    found += [r.get("wallet") for r in sorted(newborn, key=lambda r: -float(r.get("account_value") or 0))
              if float(r.get("account_value") or 0) >= 1_000_000]
    return [w for w in found if isinstance(w, str)]


def account_value(row: dict):
    """A roster row's total account value (spot + perp) when it was read, else its perp
    margin, else None. The margin alone reads $0 for a wallet whose money sits in spot."""
    evidence = row.get("evidence") or {}
    for key in ("hl_total_value", "hl_account_value"):
        value = evidence.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _orders_and_ledger(wallet: str, ms: dict, days: dict, fills: list, now_ms: int,
                       fetch) -> dict:
    """The daily reads, on the working copy `ms` of the wallet's state.

    A STOP (budget, rate limit) on either read ends the wallet. A FAILURE of one is
    returned in `errors` and the other is still read (spec 6.1: a failed read records the
    wallet as unreadable for that source). What a source folded stays folded with its
    cursor, and `orders_read_ms` is set only when both succeeded, so a failed source is
    retried next run."""
    default_start = now_ms - FIRST_READ_DAYS * records.DAY_MS
    ocursor = int(ms.get("orders_cursor_ms") or default_start)
    lcursor = int(ms.get("ledger_cursor_ms") or default_start)
    errors = []
    orders = collect.read_orders(wallet, fetch)
    if orders["ok"]:
        # Counted as soon as read: a busy bot's newest 2,000 orders can all be minutes
        # old, so waiting for them to age would never record its habits (found by the
        # 2026-10-06 dry run). An order still open is counted as open.
        start = max(ocursor, orders["oldest_ms"] or ocursor) if orders["full"] else ocursor
        if now_ms > start:
            records.fold_orders(days, orders["orders"], records.first_prices(fills),
                                wallet=wallet, role="studied", start_ms=start, end_ms=now_ms)
            ms["orders_cursor_ms"] = now_ms
    elif orders["stopped"]:
        return {"stopped": True, "errors": errors}
    else:
        errors.append({"source": "orders", "error": orders["error"]})
    ledger = collect.read_ledger(wallet, lcursor, now_ms, fetch)
    if ledger["ok"]:
        if ledger["known_until_ms"] > lcursor:
            records.fold_ledger(days, ledger["rows"], wallet=wallet, role="studied",
                                start_ms=lcursor, end_ms=ledger["known_until_ms"])
            ms["ledger_cursor_ms"] = ledger["known_until_ms"]
    elif ledger["stopped"]:
        return {"stopped": True, "errors": errors}
    else:
        errors.append({"source": "ledger", "error": ledger["error"]})
    if not errors:
        ms["orders_read_ms"] = now_ms
    return {"stopped": False, "errors": errors}


def study_wallet(wallet: str, mstate: dict, now_ms: int, data_dir: Path, fetch=None) -> dict:
    """Read and fold one wallet. `mstate` changes only for what was folded AND saved:
    the cursors move on a copy that is merged back once the days are on disk, so an
    archive that will not read leaves them where they were and the same span is read
    again after it is repaired."""
    ms = dict(mstate)
    default_start = now_ms - FIRST_READ_DAYS * records.DAY_MS
    cursor = int(ms.get("fills_cursor_ms") or default_start)
    got = collect.read_fills(wallet, cursor, now_ms, fetch)
    if not got["ok"]:
        return {"status": "stopped" if got["stopped"] else "unreadable", "error": got["error"]}
    start, last_fill = cursor, ms.get("last_fill_ms")
    if got["saturated"] and got["first_ms"]:
        # Hyperliquid no longer serves the span before the first fill read: it stays
        # unknown, never quiet, and so does whether that fill opened a session.
        start, last_fill = max(cursor, got["first_ms"]), None
    boundary, split = records.quiet_boundary([f["time"] for f in got["fills"]], start,
                                             got["known_until_ms"])
    due = now_ms - int(ms.get("orders_read_ms") or 0) >= ORDERS_EVERY_MS
    lo = start
    if due:
        lo = min(start, int(ms.get("orders_cursor_ms") or default_start),
                 int(ms.get("ledger_cursor_ms") or default_start))
    try:
        days = archive.load_days(wallet, records.day_of(lo), records.day_of(now_ms), data_dir)
        before = {day: json.dumps(record, sort_keys=True) for day, record in days.items()}
        if boundary > start:
            ms["last_fill_ms"] = records.fold_fills(
                days, got["fills"], wallet=wallet, role="studied", start_ms=start,
                end_ms=boundary, last_fill_ms=last_fill, saturated=got["saturated"],
                runs_split=split)
            ms["fills_cursor_ms"] = boundary
        elif got["saturated"]:
            ms["fills_cursor_ms"], ms["last_fill_ms"] = start, None
        result = {"status": "ok", "fills": len(got["fills"]), "saturated": got["saturated"]}
        if due:
            extra = _orders_and_ledger(wallet, ms, days, got["fills"], now_ms, fetch)
            if extra.pop("stopped"):
                result["status"] = "stopped"
            result.update(extra)
        archive.save_days({day: record for day, record in days.items()
                           if json.dumps(record, sort_keys=True) != before.get(day)}, data_dir)
    except archive.Unreadable as exc:
        return {"status": "unreadable", "error": f"archive: {exc}"}
    mstate.update(ms)
    return result


def measure_families(fams: dict, panel: dict, now_ms: int, budget, fetch=None,
                     limit: int = FAMILY_MEMBERS_PER_RUN) -> int:
    """Re-measure family members older than 30 days, a few per run (spec §8.1)."""
    members = panel.setdefault("members", {})
    due = [w for w in dict.fromkeys(m for master in sorted(fams) for m in fams[master])
           if now_ms - int((members.get(w) or {}).get("at_ms") or 0) >= FAMILY_REMEASURE_MS]
    measured = 0
    for wallet in due[:limit]:
        if not budget.can_continue():
            break
        fills = collect.read_recent_fills(wallet, fetch)
        orders = collect.read_orders(wallet, fetch) if fills["ok"] else fills
        if not orders["ok"]:
            if orders["stopped"]:
                break
            continue
        members[wallet] = {"at_ms": now_ms,
                           **tooling.measure_snapshot(fills["fills"], orders["orders"])}
        measured += 1
    return measured


def his_days(data_dir: Path, target: str) -> dict:
    """His daily records, built from the collector's stored data by the same code."""
    fills = [f for f in utils.load_all_records(str(data_dir / "fills"))
             if isinstance(f, dict) and isinstance(f.get("time"), (int, float))]
    if not fills:
        return {}
    entries = [r for r in utils.load_all_records(str(data_dir / "orders"))
               if isinstance(r, dict) and isinstance(r.get("order"), dict)]
    ledger = utils.load_all_records(str(data_dir / "ledger"))
    start = int(min(f["time"] for f in fills))
    end = int(max(f["time"] for f in fills)) + 1
    days: dict = {}
    records.fold_fills(days, fills, wallet=target, role="target", start_ms=start, end_ms=end,
                       last_fill_ms=None)
    records.fold_orders(days, entries, records.first_prices(fills), wallet=target,
                        role="target", start_ms=start, end_ms=end)
    records.fold_ledger(days, ledger, wallet=target, role="target", start_ms=start, end_ms=end)
    return days


def _mark_departed(previous: dict, members: list, now_ms: int, data_dir: Path) -> None:
    """A wallet that left the set keeps its archive; its dossier says since when."""
    current = {m["wallet"] for m in members}
    for wallet in set(previous or {}) - current:
        path = archive.root(data_dir) / "wallets" / f"{wallet}.json"
        doc = archive.read_json(path, None)
        if isinstance(doc, dict) and not doc.get("left_ms"):
            archive.write_if_changed(path, {**doc, "left_ms": now_ms})


def unreadable_row(member: dict, value, last_read_ms, ref: dict, ctx: dict) -> dict:
    """The row of a wallet whose archive will not read. Nothing is measured from it, so
    its coverage and order count are unknown (None, never 0), its tooling verdict is a
    fault to repair rather than "no evidence", and it adds nothing to the rank."""
    row = assemble.study_row(member, assemble.tooling_tests([], ref, ctx), value, last_read_ms)
    row.update(coverage_days=None, orders=None, rank=0.0, families={
        "tooling": {"verdict": "unreadable", "lr": None, "by": [], "key": {}, "basis": None}})
    return row


def run(*, data_dir: Path | None = None, config: dict | None = None, now_ms: int | None = None,
        fetch=None, read_seconds: int = READ_SECONDS) -> dict:
    data_dir = Path(data_dir or utils.DATA_DIR)
    config = config or utils.load_config()
    now_ms = int(now_ms or time.time() * 1000)
    target = config["target_wallet"].lower()
    exclude = {target, *(w.lower() for w in config.get("known_self_wallets") or [])}
    roster = read_json(data_dir / "roster" / "latest.json", {})
    state = archive.load_state(data_dir)
    wallets_state = state.setdefault("wallets", {})
    sources, state["decayed_seen"] = selection.by_source(
        config, roster, detector_wallets(data_dir), state.get("decayed_seen") or {}, now_ms)
    max_wallets = int((config.get("study") or {}).get("max_wallets") or selection.MAX_WALLETS)
    members = selection.choose(sources, state.get("members"), now_ms,
                               blocked=selection.blocked_wallets(config, roster),
                               max_wallets=max_wallets)
    _mark_departed(state.get("members"), members, now_ms, data_dir)
    state["members"] = {m["wallet"]: {"source": m["source"], "since_ms": m["since_ms"]}
                        for m in members}

    census_state = read_json(data_dir / "execution_program" / "census_state.json", {})
    fams = panels.families(read_json(data_dir / "hl_surface" / "latest.json", {}),
                           census_state.get("habits"), exclude=exclude)
    panel_path = archive.root(data_dir) / "panel" / "families.json"
    panel = read_json(panel_path, {})
    collection = {"read": [], "unreadable": [], "partial": [], "stopped": False}
    with ReadBudget(seconds=read_seconds, weight_per_minute=WEIGHT_PER_MINUTE) as budget:
        order = sorted(members, key=lambda m: (
            wallets_state.get(m["wallet"], {}).get("last_read_ms") or 0, m["wallet"]))
        for member in order:
            if not budget.can_continue():
                collection["stopped"] = True
                break
            mstate = wallets_state.setdefault(member["wallet"], {})
            result = study_wallet(member["wallet"], mstate, now_ms, data_dir, fetch)
            # Its fills were read, so the wallet counts as read; a daily read that failed is
            # reported beside it, and retried next run.
            collection["partial"] += [{"wallet": member["wallet"], **e}
                                      for e in result.get("errors") or []]
            if result["status"] == "unreadable":
                collection["unreadable"].append({"wallet": member["wallet"],
                                                 "error": result.get("error")})
                continue
            if result["status"] == "ok":
                mstate["last_read_ms"] = now_ms
                collection["read"].append(member["wallet"])
            # The cursors and last fill go to disk with the wallet's days, not at the end of
            # the run: a crash or a step timeout in the hundreds of seconds after would leave
            # the archive ahead of its state, and the recovery run would classify the first
            # uncovered order against a stale last_fill_ms (a spurious session decision).
            # A stopped wallet folded its fills too. (measure_families changes the family
            # panel, a cache that is measured again, not this state.)
            archive.save_state(state, data_dir)
            if result["status"] != "ok":
                collection["stopped"] = True
                break
        if not collection["stopped"]:
            measure_families(fams, panel, now_ms, budget, fetch)
        collection["budget"] = budget.report()
    panel["families"] = fams

    his = his_days(data_dir, target)
    ref = assemble.his_reference(his)
    splits = assemble.self_splits(his, ref)
    strangers = panels.strangers(census_state.get("habits"), exclude=exclude)
    snapshots = {w: v for w, v in (panel.get("members") or {}).items() if isinstance(v, dict)}
    ctx = assemble.panel_context(ref, splits, strangers, panels.member_pairs(fams, snapshots))
    values = {selection.address(r.get("wallet")): account_value(r)
              for r in roster.get("wallets") or [] if isinstance(r, dict)}
    first_day = records.day_of(now_ms - (WINDOW_DAYS - 1) * records.DAY_MS)
    today = records.day_of(now_ms)
    rows = []
    for member in members:
        wallet = member["wallet"]
        last_read = wallets_state.get(wallet, {}).get("last_read_ms")
        try:
            days = archive.load_days(wallet, first_day, today, data_dir)
        except archive.Unreadable as exc:
            # Tests are never computed from a partial archive (rule 5): the wallet is
            # reported, gets no dossier and no roll, and the run goes on to the next.
            if not any(u["wallet"] == wallet for u in collection["unreadable"]):
                collection["unreadable"].append({"wallet": wallet, "error": f"archive: {exc}"})
            rows.append(unreadable_row(member, values.get(wallet), last_read, ref, ctx))
            continue
        tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, ctx)
        rows.append(assemble.study_row(member, tests, values.get(wallet), last_read))
        archive.write_if_changed(archive.root(data_dir) / "wallets" / f"{wallet}.json",
                                 assemble.dossier(member, tests, ref))
        archive.roll_sealed_months(wallet, today, data_dir)
    computed_at = datetime.fromtimestamp(now_ms / 1000, UTC).isoformat()
    doc = assemble.latest_doc(computed_at, target, ref, ctx["status"], rows, collection)
    archive.write_if_changed(panel_path, panel)
    archive.save_state(state, data_dir)
    utils.save_latest(str(archive.root(data_dir)), doc)
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="read and write this data directory instead of data/ (dry runs)")
    parser.add_argument("--max-wallets", type=int, default=None)
    parser.add_argument("--read-seconds", type=int, default=READ_SECONDS)
    args = parser.parse_args(argv)
    config = utils.load_config()
    if args.max_wallets:
        config = {**config, "study": {**(config.get("study") or {}),
                                      "max_wallets": args.max_wallets}}
    doc = run(data_dir=args.data_dir, config=config, read_seconds=args.read_seconds)
    partial = len({p["wallet"] for p in doc["partial"]})
    print(f"[study] {doc['studied']} studied, {doc['read']} read, "
          f"{len(doc['unreadable'])} unreadable, {partial} partial, stopped={doc['stopped']}; "
          f"panels {doc['panels']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
