"""Available fill history with explicit gaps and an incremental local cache."""

import hashlib
import json
import sqlite3
from pathlib import Path

from src.candidate_registry import valid_wallet
from src.utils import DATA_DIR, hl_read

PAGE_SIZE = 2000
RETENTION_LIMIT = 10_000


class FillBatch(list):
    """Compatible list carrying the read's status, including on cached data."""

    def __init__(self, result):
        super().__init__(result["fills"])
        self.read = {k: v for k, v in result.items() if k != "fills"}


def fill_id(wallet, row):
    if row.get("tid") is not None:
        return f"{wallet}:{row.get('coin')}:{row['time']}:{row['tid']}"
    return hashlib.sha256((wallet + json.dumps(row, sort_keys=True)).encode()).hexdigest()


def fetch_fill_history(wallet: str, start_ms: int, end_ms: int, fetch=None,
                       max_pages: int = 5) -> dict:
    wallet = valid_wallet(wallet)
    fetch = fetch or hl_read
    cursor = start_ms
    rows, gaps = {}, []
    error, complete, pages = None, False, 0
    for _ in range(max(0, max_pages)):
        pages += 1
        try:
            response = fetch({"type": "userFillsByTime", "user": wallet,
                              "startTime": cursor, "endTime": end_ms,
                              "aggregateByTime": False})
            if isinstance(response, dict) and "ok" in response:
                if not response["ok"]:
                    raise ValueError(response.get("error") or "read failed")
                response = response.get("data")
            if not isinstance(response, list):
                raise ValueError("expected fills array")
            valid = []
            for row in response:
                if not isinstance(row, dict) or not isinstance(row.get("time"), (int, float)):
                    raise ValueError("invalid fill timestamp")
                if start_ms <= row["time"] <= end_ms:
                    valid.append(row)
                    rows[fill_id(wallet, row)] = row
            if len(response) < PAGE_SIZE:
                complete = True
                break
            latest = max((r["time"] for r in valid), default=cursor)
            if latest <= cursor:
                gaps.append({"start_ms": cursor, "reason": "timestamp_saturation"})
                break
            # Inclusive overlap avoids silently skipping other fills at the boundary.
            cursor = int(latest)
        except (ValueError, TypeError, OSError, RuntimeError) as exc:
            error = str(exc)[:300]
            gaps.append({"start_ms": cursor, "reason": "read_error"})
            break
    else:
        gaps.append({"start_ms": cursor, "reason": "page_budget"})
    fills = sorted(rows.values(), key=lambda r: (r["time"], fill_id(wallet, r)))
    return {"fills": fills, "status": "ok" if complete else ("partial" if fills else "error"),
            "next_cursor_ms": cursor,
            "coverage": {"start_ms": start_ms, "end_ms": end_ms, "pages": pages,
                         "complete_available_window": complete,
                         "server_retention_limit": RETENTION_LIMIT,
                         "older_history_available": None},
            "saturation": any(g["reason"] == "timestamp_saturation" for g in gaps),
            "gaps": gaps, "error": error}


def cached_fill_history(wallet: str, start_ms: int, end_ms: int, *,
                        db_path: Path | None = None, fetch=None, max_pages: int = 5) -> dict:
    wallet = valid_wallet(wallet)
    path = Path(db_path or DATA_DIR / ".local" / "discovery.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS fills (wallet TEXT, event_id TEXT PRIMARY KEY, ts INTEGER, raw TEXT)")
        db.execute("CREATE INDEX IF NOT EXISTS fills_wallet_time ON fills(wallet, ts)")
        db.execute("CREATE TABLE IF NOT EXISTS fill_coverage (wallet TEXT PRIMARY KEY, start_ms INTEGER, end_ms INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS fill_progress (wallet TEXT PRIMARY KEY, start_ms INTEGER, cursor_ms INTEGER)")
        prior = db.execute("SELECT start_ms,end_ms FROM fill_coverage WHERE wallet=?", (wallet,)).fetchone()
        cursor = max(start_ms, prior[1] - 60_000) if prior and start_ms >= prior[0] else start_ms
        pending = db.execute("SELECT start_ms,cursor_ms FROM fill_progress WHERE wallet=?", (wallet,)).fetchone()
        # Resume only a compatible window. A wider lookback must first collect
        # its missing prefix; a historical query must not jump beyond its end.
        if pending and start_ms >= pending[0] and pending[1] <= end_ms:
            cursor = max(cursor, pending[1])
        result = fetch_fill_history(wallet, cursor, end_ms, fetch=fetch, max_pages=max_pages)
        db.executemany("INSERT OR REPLACE INTO fills VALUES (?,?,?,?)",
                       [(wallet, fill_id(wallet, r), r["time"], json.dumps(r)) for r in result["fills"]])
        if result["status"] == "ok":
            # Only join windows that overlap. A disjoint window does not fill the gap.
            overlaps = prior and start_ms <= prior[1] and end_ms >= prior[0]
            covered_start = min(start_ms, prior[0]) if overlaps else start_ms
            covered_end = max(end_ms, prior[1]) if overlaps else end_ms
            db.execute("INSERT OR REPLACE INTO fill_coverage VALUES (?,?,?)", (wallet, covered_start, covered_end))
            prior = (covered_start, covered_end)
            db.execute("DELETE FROM fill_progress WHERE wallet=?", (wallet,))
        else:
            # Rows and continuation commit together. Never advance beyond a
            # fully processed page or skip a saturated timestamp boundary.
            db.execute("INSERT OR REPLACE INTO fill_progress VALUES (?,?,?)",
                       (wallet, start_ms, result['next_cursor_ms']))
        # Retain at most 50k observed fills per enriched candidate. This limit is
        # separate from the upstream 10k limit and is declared to consumers.
        trimmed = db.execute("DELETE FROM fills WHERE wallet=? AND event_id NOT IN "
                             "(SELECT event_id FROM fills WHERE wallet=? ORDER BY ts DESC LIMIT 50000)",
                             (wallet, wallet)).rowcount
        if trimmed:
            # Retained data can no longer substantiate the full cached prefix.
            db.execute('DELETE FROM fill_coverage WHERE wallet=?', (wallet,))
            db.execute('DELETE FROM fill_progress WHERE wallet=?', (wallet,))
            prior = None
            result['status'] = 'partial'
            result['coverage']['complete_available_window'] = False
            result['gaps'].append({'start_ms': start_ms, 'reason': 'local_retention'})
        result["fills"] = [json.loads(row[0]) for row in db.execute(
            "SELECT raw FROM fills WHERE wallet=? AND ts>=? AND ts<=? ORDER BY ts,event_id", (wallet, start_ms, end_ms))]
        result["last_successful_end_ms"] = prior[1] if prior else None
        result["coverage"].update(start_ms=start_ms, fetched_start_ms=cursor,
                                  requested_start_ms=start_ms, cached=True, local_retention_fills=50_000)
    return result
