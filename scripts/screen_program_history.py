#!/usr/bin/env python3
"""Screen every past day of his markets for his script (`src/program_screen.py`).

Each run reads a bounded number of unread days from SQD's fill feed — his silent
days first, newest first, then his active days as the control — judges every
wallet of the day against what he looked like BEFORE that day, and keeps only
the day's counts and hits in `data/tape/program_screen.json`.

A hit (his cadence AND three of his sizes, on a wallet that is not his) is
registered as a `program_screen` candidate, which the execution-program detector
reads through its registry slice, and paged HIGH once a week per wallet — our
inference, measured rare (the 2026-11-13 control: 1 of 67,044, himself).

    python scripts/screen_program_history.py --max-days 1 --time-budget 300
    python scripts/screen_program_history.py --report

Strict: a day that cannot be read whole is left unread and retried, never stored
as a quiet day (rule 5).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import execution_program as ep  # noqa: E402
from src import fills_feed as ff  # noqa: E402
from src import program_screen as ps  # noqa: E402
from src.utils import DATA_DIR, atomic_write_json, load_config  # noqa: E402

OUT = DATA_DIR / "tape" / "program_screen.json"
INDEX_FILE = DATA_DIR / "state" / "sqd_block_index.json"
CHUNK_MINUTES = 60
MAX_HIT_DAYS = 60          # days remembered per hit wallet
ALERT_COOLDOWN_HOURS = 168


def load_doc(path: Path = OUT) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (FileNotFoundError, ValueError):
        doc = {}
    doc.setdefault("days", {})
    doc.setdefault("hits", {})
    return doc


def load_index(path: Path = INDEX_FILE) -> ff.BlockIndex:
    try:
        with open(path, encoding="utf-8") as handle:
            return ff.BlockIndex(json.load(handle))
    except (FileNotFoundError, ValueError):
        return ff.BlockIndex()


def own_orders() -> list[dict]:
    from src.fingerprint import load_fills
    return ep.reconstruct_orders(load_fills())


def read_day(day: str, coins: list[str], index: ff.BlockIndex, *, deadline: float,
             stream=ff.stream, post=None) -> dict:
    """Fold the whole day. Raises `ff.FeedError` if it cannot be read whole."""
    lo = ps.day_start_ms(day)
    hi = lo + ps.DAY_MS - 1
    from_block, to_block = index.block_at(lo, post=post), index.block_at(hi, post=post)
    state = ps.empty_state()
    step = ff.blocks_for_minutes(CHUNK_MINUTES)
    cursor = from_block
    read_bytes = requests_made = 0
    while cursor <= to_block:
        if time.time() > deadline:
            raise ff.FeedError(f"{day}: time budget reached at block {cursor}")
        end = min(cursor + step - 1, to_block)
        out = stream(cursor, end, coins=coins, post=post)
        read_bytes += out.get("bytes") or 0
        requests_made += out.get("requests") or 0
        fills = [f for f in out["fills"] if lo <= f["time"] <= hi]
        ps.fold(state, ff.taker_orders(fills))
        last = out.get("last_block")
        if out.get("complete"):
            cursor = end + 1
        elif last is not None and last >= cursor:
            cursor = last + 1
        else:
            raise ff.FeedError(f"{day}: no progress at block {cursor}")
    return {"state": state, "bytes": read_bytes, "requests": requests_made}


def record_hits(doc: dict, day: str, silent: bool, hits: list[dict]) -> list[dict]:
    """Fold a day's hits into the per-wallet memory; returns the wallets that
    are NEW, or newly seen on a silent day."""
    fresh = []
    for row in hits:
        entry = doc["hits"].setdefault(row["wallet"], {"days": [], "silent_days": [], "best": None})
        new = not entry["days"] or (silent and not entry["silent_days"])
        if day not in entry["days"]:
            entry["days"] = sorted({*entry["days"], day})[-MAX_HIT_DAYS:]
        if silent and day not in entry["silent_days"]:
            entry["silent_days"] = sorted({*entry["silent_days"], day})[-MAX_HIT_DAYS:]
        best = entry.get("best")
        if not best or (row["clips_matched"], row["notional"]) > (best["clips_matched"], best["notional"]):
            entry["best"] = {**row, "day": day}
        if new:
            fresh.append({**row, "day": day, "silent": silent})
    return fresh


def register(fresh: list[dict], data_dir=None) -> list[str]:
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for row in fresh:
        try:
            wallet = valid_wallet(row["wallet"])
        except ValueError:
            continue
        observe_candidate(wallet, {
            "source": "program_screen", "positive": True, "event_id": f"program-screen:{wallet}",
            "detail": {k: row.get(k) for k in ("day", "silent", "cadence", "clips_matched",
                                               "clips_matched_coins", "notional")},
        }, data_dir=data_dir)
        registered.append(wallet)
    return registered


def page(fresh: list[dict]) -> None:
    from src import alerts
    for row in fresh:
        when = ("on a day he was SILENT" if row.get("silent") else "on a day he also traded")
        alerts._send_with_cooldown(
            f"program_screen:{row['wallet']}", ALERT_COOLDOWN_HOURS,
            f"HIGH: His Script Seen On Another Wallet ({row['wallet'][:10]})",
            f"{row['wallet']} ran a loop paced like his "
            f"(p25 {row['cadence']['p25']}s, median {row['cadence']['p50']}s) at his own sizes on "
            f"{row['clips_matched']} coins ({', '.join(row['clips_matched_coins'][:8])}) on "
            f"{row['day']}, {when}; ${row['notional']:,} of taker notional that day.\n"
            f"Measured rare: on the 2026-11-13 control only his own wallet did both, of 67,044. "
            f"A behaviour match is evidence, not ownership (rule 8): look at its funding and birth.\n"
            f"https://hypurrscan.io/address/{row['wallet']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-days", type=int, default=1)
    parser.add_argument("--time-budget", type=int, default=300)
    parser.add_argument("--day", help="read this one day (YYYY-MM-DD) regardless of the plan")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--no-alert", action="store_true")
    args = parser.parse_args(argv)

    doc = load_doc()
    if args.report:
        print(json.dumps({"summary": doc.get("summary"), "hits": doc.get("hits")}, indent=1))
        return 0

    config = load_config()
    target = str(config.get("target_wallet") or "").lower()
    known = {str(w).lower() for w in config.get("known_self_wallets") or []}
    orders = own_orders()
    if not orders:
        print("[screen] no stored fills of his - nothing to compare against")
        return 1
    his_days = {ps.day_of(o["t"]) for o in orders}
    first_day = ps.day_of(orders[0]["t"] + ps.MIN_REFERENCE_DAYS * ps.DAY_MS)
    last_day = ps.day_of(int(time.time() * 1000) - 2 * ps.DAY_MS)     # a day SQD has whole
    done = {d for d, row in doc["days"].items() if not (row or {}).get("error")}
    queue = ([(args.day, args.day not in his_days)] if args.day
             else ps.plan(his_days, first_day, last_day, done))
    index = load_index()
    started = time.time()
    deadline = started + args.time_budget
    read = 0
    for day, silent in queue:
        if read >= args.max_days or time.time() > deadline - 30:
            break
        ref = ps.reference(orders, ps.day_start_ms(day))
        coins = ps.coins_to_read(ref)
        if not ref["cadence"] or len(coins) < ps.MIN_HIT_CLIPS:
            doc["days"][day] = {"silent": silent, "skipped": "no reference yet",
                                "read_at": datetime.now(UTC).isoformat()}
            continue
        try:
            got = read_day(day, coins, index, deadline=deadline)
        except ff.FeedError as exc:
            # Left unread: the next run retries it. Never a quiet day.
            print(f"[screen] {day} not read: {exc}")
            doc["days"][day] = {"silent": silent, "error": str(exc)[:200],
                                "read_at": datetime.now(UTC).isoformat()}
            break
        verdict = ps.judge_day(got["state"], ref, target=target, exclude=known)
        fresh = record_hits(doc, day, silent, verdict["hits"])
        doc["days"][day] = {
            "silent": silent, "read_at": datetime.now(UTC).isoformat(),
            "coins": len(coins), "bytes": got["bytes"], "requests": got["requests"],
            **{k: verdict[k] for k in ("wallets", "judged", "cadence_hits", "clip_hits",
                                       "target_seen", "target_found", "near")},
            "hits": [h["wallet"] for h in verdict["hits"]],
            "reference_cadence": ref["cadence"],
        }
        read += 1
        print(f"[screen] {day} ({'silent' if silent else 'active'}): {verdict['wallets']} wallets, "
              f"{verdict['judged']} judged, {verdict['cadence_hits']} paced like him, "
              f"{verdict['clip_hits']} at his sizes, {len(verdict['hits'])} BOTH; "
              f"him: {verdict['target_found']}; {got['bytes'] / 1e9:.2f} GB "
              f"in {time.time() - started:.0f}s")
        if fresh:
            register(fresh)
            if not args.no_alert:
                page(fresh)
        del got
    doc["summary"] = ps.summarise(doc["days"], doc["hits"])
    doc["computed_at"] = datetime.now(UTC).isoformat()
    doc["pending_days"] = max(0, len(queue) - read) if not args.day else None
    atomic_write_json(OUT, doc)
    atomic_write_json(INDEX_FILE, [list(p) for p in index.points], compact=True)
    print(f"[screen] {json.dumps(doc['summary'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
