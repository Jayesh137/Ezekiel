"""Strict, budgeted reads of one wallet for the study (spec §6.1). No folding here.

Every reader distinguishes three outcomes: a read (ok), a refusal by the run's
ReadBudget or a 429 (stopped: the run ends and the wallet goes first next time),
and a failure (the wallet is unreadable this run). A failed read never looks like
an empty one (rule 5): it returns no rows and no `known_until_ms`.
"""

from __future__ import annotations

PAGE = 2_000
MAX_PAGES = 5
LEDGER_MAX_PAGES = 3
BUDGET_ERRORS = ("time_budget", "rate_limited", "rate limited")


def _fetch(fetch):
    if fetch is not None:
        return fetch
    from src.utils import hl_read
    return hl_read


def _call(fetch, body: dict) -> dict:
    result = _fetch(fetch)(body)
    if not isinstance(result, dict) or "ok" not in result:
        return {"ok": False, "data": None, "error": "malformed read result", "stopped": False}
    if result["ok"]:
        return {"ok": True, "data": result.get("data"), "error": None, "stopped": False}
    error = str(result.get("error") or "read failed")
    return {"ok": False, "data": None, "error": error, "stopped": error in BUDGET_ERRORS}


def _timed_rows(page) -> bool:
    return isinstance(page, list) and all(
        isinstance(r, dict) and isinstance(r.get("time"), (int, float)) for r in page)


def _fill_key(row: dict):
    return row["tid"] if row.get("tid") is not None else (
        row.get("oid"), row["time"], row.get("px"), row.get("sz"))


def _failed(got: dict, **empty) -> dict:
    return {"ok": False, "stopped": got.get("stopped", False), "error": got.get("error"), **empty}


def read_fills(wallet: str, start_ms: int, now_ms: int, fetch=None,
               max_pages: int = MAX_PAGES) -> dict:
    """Fills since start_ms, oldest first, aggregated by time.

    `known_until_ms`: every fill up to it has been seen. `saturated`: Hyperliquid
    keeps only an account's newest 10,000 fills, so when five full pages come back
    the span between start_ms and the first fill read may be missing.
    """
    empty = {"fills": [], "known_until_ms": None, "saturated": False, "first_ms": None}
    rows, cursor, pages, full_pages, complete = {}, int(start_ms), 0, 0, False
    while pages < max_pages:
        pages += 1
        got = _call(fetch, {"type": "userFillsByTime", "user": wallet, "startTime": cursor,
                            "endTime": int(now_ms), "aggregateByTime": True})
        if not got["ok"]:
            return _failed(got, pages=pages, **empty)
        page = got["data"]
        if not _timed_rows(page):
            return _failed({"error": "unexpected fills shape"}, pages=pages, **empty)
        for row in page:
            rows[_fill_key(row)] = row
        if len(page) < PAGE:
            complete = True
            break
        full_pages += 1
        latest = int(max(r["time"] for r in page))
        if latest <= cursor:
            break  # 2,000 fills in one millisecond: cannot page past it
        cursor = latest
    fills = sorted(rows.values(), key=lambda r: (r["time"], str(r.get("tid"))))
    if complete:
        known_until = int(now_ms)
    else:
        known_until = int(fills[-1]["time"]) if fills else int(start_ms)
    return {"ok": True, "stopped": False, "error": None, "fills": fills,
            "known_until_ms": known_until, "saturated": full_pages >= max_pages,
            "first_ms": int(fills[0]["time"]) if fills else None, "pages": pages}


def read_recent_fills(wallet: str, fetch=None) -> dict:
    """The newest 2,000 fills (`userFills`), for one-off panel snapshots."""
    got = _call(fetch, {"type": "userFills", "user": wallet})
    if not got["ok"]:
        return _failed(got, fills=[])
    if not _timed_rows(got["data"]):
        return _failed({"error": "unexpected fills shape"}, fills=[])
    return {"ok": True, "stopped": False, "error": None, "fills": got["data"]}


def read_orders(wallet: str, fetch=None) -> dict:
    """The newest 2,000 orders. With a full page, orders placed before `oldest_ms`
    may be missing, so the caller vouches only from there."""
    got = _call(fetch, {"type": "historicalOrders", "user": wallet})
    empty = {"orders": [], "oldest_ms": None, "full": False}
    if not got["ok"]:
        return _failed(got, **empty)
    data = got["data"]
    if not isinstance(data, list) or not all(
            isinstance(r, dict) and isinstance(r.get("order"), dict) for r in data):
        return _failed({"error": "unexpected orders shape"}, **empty)
    stamps = [r["order"]["timestamp"] for r in data
              if isinstance(r["order"].get("timestamp"), (int, float))]
    return {"ok": True, "stopped": False, "error": None, "orders": data,
            "oldest_ms": int(min(stamps)) if stamps else None, "full": len(data) >= PAGE}


def read_ledger(wallet: str, start_ms: int, now_ms: int, fetch=None,
                max_pages: int = LEDGER_MAX_PAGES) -> dict:
    """Non-funding ledger updates (deposits, withdrawals, sends) since start_ms."""
    rows, cursor, complete = {}, int(start_ms), False
    for _ in range(max_pages):
        got = _call(fetch, {"type": "userNonFundingLedgerUpdates", "user": wallet,
                            "startTime": cursor, "endTime": int(now_ms)})
        if not got["ok"]:
            return _failed(got, rows=[], known_until_ms=None)
        page = got["data"]
        if not _timed_rows(page):
            return _failed({"error": "unexpected ledger shape"}, rows=[], known_until_ms=None)
        for row in page:
            rows[(row.get("hash"), row["time"], str((row.get("delta") or {}).get("type")))] = row
        if len(page) < PAGE:
            complete = True
            break
        latest = int(max(r["time"] for r in page))
        if latest <= cursor:
            break
        cursor = latest
    ordered = sorted(rows.values(), key=lambda r: r["time"])
    if complete:
        known_until = int(now_ms)
    else:
        known_until = int(ordered[-1]["time"]) if ordered else int(start_ms)
    return {"ok": True, "stopped": False, "error": None, "rows": ordered,
            "known_until_ms": known_until}
