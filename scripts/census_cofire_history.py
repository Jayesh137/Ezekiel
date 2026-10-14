#!/usr/bin/env python3
"""Co-firing census over his whole stored history (src/cofire_history.py).

Reads his sessions from the stored fills, fetches every fill on the session's
coins around it from SQD's keyless portal (src/fills_feed.py), folds each
window with `cofire.fold_window` — the target ALONE as "him", so his known
wallets are positive controls — and writes `data/tape/cofire_history.json`:
the state, the sessions read, the controls, the population's base rate and the
hits. Hits are registered as `tape_cofire` candidates exactly as the live
screen does, so the per-wallet detectors examine them next trace run.

Resumable: a session is remembered by its key (start time + coin set), the
state is written after every session, and `--max-sessions` bounds a run. The
block index found by bisection is cached in `data/state/sqd_block_index.json`.
Strict: a portal failure raises and the cursor does not move past the session.

    python scripts/census_cofire_history.py                 # everything unread
    python scripts/census_cofire_history.py --max-sessions 6
    python scripts/census_cofire_history.py --report        # print, read nothing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import cofire
from src import cofire_history as ch
from src import fills_feed as ff
from src.utils import DATA_DIR, atomic_write_json, load_config

OUT_FILE = DATA_DIR / "tape" / "cofire_history.json"
INDEX_FILE = DATA_DIR / "state" / "sqd_block_index.json"
# Per-window read bounds: a padded hour of a handful of coins is a few MB.
MAX_REQUESTS_PER_WINDOW = 48
MAX_BYTES_PER_WINDOW = 60_000_000
DEFAULT_TIME_BUDGET = 240


def load_state(path: Path = OUT_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except FileNotFoundError:
        return {"state": cofire.empty_state(), "read": {}, "incomplete": {}}
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON, refusing to restart the census: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance((doc.get("state") or {}).get("wallets"), dict):
        raise ValueError(f"{path}: unexpected shape, refusing to restart the census")
    doc.setdefault("read", {})
    doc.setdefault("incomplete", {})
    return doc


def load_index(path: Path = INDEX_FILE) -> ff.BlockIndex:
    try:
        with open(path, encoding="utf-8") as handle:
            return ff.BlockIndex(json.load(handle))
    except (FileNotFoundError, ValueError):
        return ff.BlockIndex()


def save_index(index: ff.BlockIndex, path: Path = INDEX_FILE) -> None:
    atomic_write_json(path, [list(p) for p in index.points], compact=True)


def known_wallets(config: dict) -> set:
    return {str(w).lower() for w in (config.get("known_self_wallets") or []) if isinstance(w, str)}


def read_window(index: ff.BlockIndex, head, from_ms: int, to_ms: int, coins: list[str], *,
                post=None) -> dict:
    """Every fill on `coins` between the two times, as taker orders."""
    from_block = index.block_at(from_ms, post=post, head=head)
    to_block = index.block_at(to_ms, post=post, head=head)
    if to_block < from_block:
        to_block = from_block
    out = ff.stream(from_block, to_block, coins=coins, post=post,
                    max_requests=MAX_REQUESTS_PER_WINDOW, max_bytes=MAX_BYTES_PER_WINDOW)
    if not out.get("complete"):
        raise ff.FeedError(f"window {from_block}-{to_block} read incompletely "
                           f"({out.get('requests')} requests, {out.get('bytes')} bytes)")
    fills = [f for f in out["fills"] if from_ms <= f["time"] <= to_ms]
    return {"orders": ff.taker_orders(fills), "fills": len(fills), "from_block": from_block,
            "to_block": to_block, "requests": out["requests"], "bytes": out["bytes"]}


def register(hits: list[dict], data_dir=None) -> list[str]:
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for hit in hits:
        try:
            wallet = valid_wallet(hit["wallet"])
        except ValueError:
            continue
        observe_candidate(wallet, {
            "source": "tape_cofire", "positive": True, "event_id": f"cofire-history:{wallet}",
            "detail": {k: hit.get(k) for k in ("events", "expected", "lift", "z", "lead",
                                               "tie", "follow", "lead_share", "bursts", "coins")},
        }, data_dir=data_dir)
        registered.append(wallet)
    return registered


def report(doc: dict, config: dict, all_sessions: list[dict]) -> dict:
    target = str(config.get("target_wallet") or "").lower()
    known = known_wallets(config)
    state = doc["state"]
    hits = cofire.screen(state, exclude={target} | known, limit=100)
    out = {
        "computed_at": datetime.now(UTC).isoformat(),
        "target": target,
        "coverage": ch.coverage(state, set(doc["read"]), all_sessions),
        "controls": ch.controls(state, known),
        "base_rate": ch.base_rate(state, exclude={target} | known),
        "hits": hits,
        "hit_coins": ch.by_coin(state, hits),
        "summary": cofire.summary(state, hits),
        "incomplete": doc.get("incomplete") or {},
    }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-sessions", type=int, default=0, help="0 = every unread session")
    parser.add_argument("--time-budget", type=int, default=DEFAULT_TIME_BUDGET)
    parser.add_argument("--report", action="store_true", help="print the current reading only")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)

    config = load_config()
    target = str(config.get("target_wallet") or "").lower()
    from src.fingerprint import load_fills
    orders = ch.his_orders(load_fills(), target)
    all_sessions = ch.sessions(orders)
    doc = load_state()
    print(f"[cofire-history] {len(orders)} taker orders of his in {len(all_sessions)} sessions; "
          f"{len(doc['read'])} read so far")

    if not args.report:
        index = load_index()
        head_block = ff.head()
        head = ff.header_at(max(ff.START_BLOCK, head_block - 50))
        index.add(*head)
        started = time.time()
        done = 0
        pending = [s for s in all_sessions if s["key"] not in doc["read"]]
        for session in pending:
            if args.max_sessions and done >= args.max_sessions:
                break
            if time.time() - started > args.time_budget:
                print("[cofire-history] time budget reached")
                break
            windows = ch.slices(session)
            when = datetime.fromtimestamp(session["start"] / 1000, UTC).strftime("%Y-%m-%d %H:%M")
            try:
                folded = []
                for from_ms, to_ms in windows:
                    read = read_window(index, head, from_ms, to_ms, session["coins"])
                    window = ch.fold_session(doc["state"], read["orders"], target, session["start"])
                    folded.append({**window, "fills": read["fills"], "requests": read["requests"]})
            except ff.FeedError as exc:
                doc["incomplete"][session["key"]] = str(exc)[:200]
                print(f"[cofire-history] {when} {session['coins']}: NOT READ ({exc})")
                save_index(index)
                atomic_write_json(OUT_FILE, {**doc, **report(doc, config, all_sessions)}, compact=True)
                continue
            doc["incomplete"].pop(session["key"], None)
            doc["read"][session["key"]] = {"at": datetime.now(UTC).isoformat(),
                                           "windows": len(windows),
                                           "his_orders": sum(f["his_orders"] for f in folded),
                                           "events": sum(f["events"] for f in folded),
                                           "fills": sum(f["fills"] for f in folded)}
            done += 1
            print(f"[cofire-history] {when} {','.join(session['coins'])[:60]}: "
                  f"{session['orders']} of his, {doc['read'][session['key']]['fills']} fills on tape, "
                  f"{doc['read'][session['key']]['events']} events, "
                  f"{sum(f['wallets'] for f in folded)} wallets co-fired")
            cofire.cap(doc["state"], limit=cofire.MAX_WALLETS * 3)
            save_index(index)
            atomic_write_json(OUT_FILE, {**doc, **report(doc, config, all_sessions)}, compact=True)
        print(f"[cofire-history] read {done} session(s) this run, "
              f"{len(doc['read'])}/{len(all_sessions)} in all")

    out = report(doc, config, all_sessions)
    if not args.report:
        registered = [] if args.no_register else register(out["hits"])
        out["registered"] = registered
        atomic_write_json(OUT_FILE, {**doc, **out}, compact=True)
    cov, rate = out["coverage"], out["base_rate"]
    print(f"[cofire-history] coverage {cov['sessions_read']}/{cov['sessions']} sessions, "
          f"{cov['orders_read']}/{cov['orders']} of his orders")
    for control in out["controls"]:
        print(f"[cofire-history] control {control['wallet']}: "
              + ("never on his tape" if not control.get("seen") else
                 f"{control['events']} events, lift {control['lift']}, lead share "
                 f"{control['lead_share']}, {control['bursts']} bursts, hit={control['hit']}"))
    print(f"[cofire-history] base rate: {rate['wallets']} wallets with >={rate['min_events']} events, "
          f"{rate['hits']} clear the bar"
          + (f" ({100 * rate['hit_rate']:.2f}%)" if rate["hit_rate"] is not None else " (unmeasured)")
          + f"; lift deciles {rate['lift_deciles']}; lead-share deciles {rate['lead_share_deciles']}")
    for hit in out["hits"][:15]:
        print(f"[cofire-history] HIT {hit['wallet']} events={hit['events']} lift={hit['lift']} "
              f"z={hit['z']} lead/tie/follow={hit['lead']}/{hit['tie']}/{hit['follow']} "
              f"bursts={hit['bursts']} coins={','.join(hit['coins'])[:50]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
