# src/newborn.py
"""Fresh Hyperliquid accounts, found from the leaderboard alone.

The leaderboard returns every account with performance history — 45,005 rows
on 2026-09-10 — with volume over the day, week, month and all time. An
account whose all-time volume equals its month volume did all its trading in
the last thirty days: it was born within the month. No per-wallet call is
needed to know that.

This matters because the behavioural sweep scans the 500 largest accounts,
and a migrated wallet is small until it is not. Measured live: 84 accounts
above $1M were born within thirty days, including a correlation lead with
$51.6M. Birth is orthogonal to size, and it is the one selection that catches
a fresh wallet early.

Pure: `newborn_rows` and `first_appearances` take the rows and a seen-map.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

NEWBORN_DIR = DATA_DIR / "newborn"

# Volumes that agree to within this fraction count as equal.
EQUAL_TOL = 0.001


def _f(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x and x not in (float("inf"), float("-inf")) else 0.0


def _window(row: dict, name: str) -> dict:
    for item in row.get("windowPerformances") or []:
        if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == name:
            return item[1] if isinstance(item[1], dict) else {}
    return {}


def age_class(row: dict) -> str | None:
    """'week', 'month', or None when the account's history predates a month.

    Equal all-time and window volumes mean every dollar traded happened inside
    the window. Zero all-time volume says nothing and returns None.
    """
    all_v = _f(_window(row, "allTime").get("vlm"))
    if all_v <= 0:
        return None
    if abs(all_v - _f(_window(row, "week").get("vlm"))) / all_v < EQUAL_TOL:
        return "week"
    if abs(all_v - _f(_window(row, "month").get("vlm"))) / all_v < EQUAL_TOL:
        return "month"
    return None


def newborn_rows(rows: list[dict], min_value_usd: float = 1_000_000.0) -> list[dict]:
    """Accounts born within a month holding at least `min_value_usd`."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        addr = (row.get("ethAddress") or row.get("address") or "").lower()
        if not addr:
            continue
        value = _f(row.get("accountValue"))
        if value < min_value_usd:
            continue
        age = age_class(row)
        if not age:
            continue
        out.append({"wallet": addr, "account_value": round(value, 2), "age": age,
                    "all_time_volume": round(_f(_window(row, "allTime").get("vlm")), 2),
                    "all_time_pnl": round(_f(_window(row, "allTime").get("pnl")), 2),
                    "display_name": row.get("displayName")})
    out.sort(key=lambda r: (0 if r["age"] == "week" else 1, -r["account_value"]))
    return out


def first_appearances(rows: list[dict], seen: dict, now_iso: str) -> tuple[dict, list]:
    """Update the seen-map (address -> first seen) and return the new addresses."""
    seen = dict(seen or {})
    new = []
    for row in rows or []:
        addr = (row.get("ethAddress") or row.get("address") or "").lower() \
            if isinstance(row, dict) else ""
        if addr and addr not in seen:
            seen[addr] = now_iso
            new.append(addr)
    return seen, new


def build_report(rows: list[dict], seen: dict, now: datetime | None = None,
                 min_value_usd: float = 1_000_000.0) -> tuple[dict, dict]:
    now = now or datetime.now(UTC)
    seen, new = first_appearances(rows, seen, now.isoformat())
    born = newborn_rows(rows, min_value_usd)
    newset = set(new)
    for b in born:
        b["first_seen_this_run"] = b["wallet"] in newset
    report = {
        "computed_at": now.isoformat(),
        "leaderboard_rows": len(rows or []),
        "known_addresses": len(seen),
        "first_seen_this_run": len(new),
        "min_value_usd": min_value_usd,
        "newborn": born,
    }
    return report, seen


def save(report: dict, seen: dict) -> None:
    save_latest(str(NEWBORN_DIR), report)
    from src.utils import atomic_write_json
    atomic_write_json(NEWBORN_DIR / "seen.json", seen)
