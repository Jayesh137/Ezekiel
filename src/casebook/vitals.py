"""Hyperliquid vitals for a suspect from one `portfolio` read (spec 2026-10-08 §8). Pure.

`portfolio` answers for ANY address: eight windows of eleven synthetic 0.0 points
when Hyperliquid has never heard of it (measured 2026-10-08), the account's whole
value history from birth when it has. One weight-20 call gives its life story:
birth, value now, and day/week/month/all-time volume.

A first probe is a baseline. Only what the life series shows happening in the
last seven days can be an event, so the first sweep of a thousand cases is not a
thousand alerts.
"""

from __future__ import annotations

import math

from src.casebook.cases import day, iso
from src.hl_identity import parse_birth

LIFE_POINTS = 60
MAX_PROBES = 120
GROW_TO_USD = 1_000_000.0
GROW_FROM_BELOW_USD = 250_000.0
EMPTY_FROM_USD = 100_000.0
EMPTY_BELOW_USD = 1_000.0
RECENT_MS = 7 * 86_400_000
# The windows that hold spot AND perp (perpDay etc. are perp only), as hl_identity reads them.
WINDOWS = {"day": "day_volume", "week": "week_volume", "month": "month_volume", "allTime": "all_time_volume"}
FIELDS = ("on_hl", "birth_ms", "total_value", "day_volume", "week_volume", "month_volume", "all_time_volume")


def _finite(raw) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _downsample(points: list, n: int) -> list:
    if len(points) <= n:
        return [[ts, round(v, 2)] for ts, v in points]
    step = (len(points) - 1) / (n - 1)
    picked = sorted({round(i * step) for i in range(n)})
    return [[points[i][0], round(points[i][1], 2)] for i in picked]


def parse_portfolio(payload) -> dict | None:
    """The vitals in one `portfolio` answer; None when it is not one (a failed read)."""
    if not isinstance(payload, list):
        return None
    out = {"on_hl": False, "birth_ms": None, "total_value": None, "day_volume": None,
           "week_volume": None, "month_volume": None, "all_time_volume": None, "life": []}
    newest, windows = None, 0
    for item in payload:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        name, series = item
        if name not in WINDOWS or not isinstance(series, dict):
            continue
        windows += 1
        out[WINDOWS[name]] = _finite(series.get("vlm"))
        history = series.get("accountValueHistory")
        points = []
        for point in history if isinstance(history, list) else []:
            try:
                ts, value = int(point[0]), _finite(point[1])
            except (TypeError, ValueError, IndexError, KeyError, OverflowError):
                continue
            if value is None:
                continue
            points.append((ts, value))
            if newest is None or ts > newest[0]:
                newest = (ts, value)
        if name == "allTime":
            out["life"] = _downsample(sorted(points), LIFE_POINTS)
    if windows == 0:
        return None
    out["total_value"] = newest[1] if newest else None
    out["birth_ms"] = parse_birth(payload)
    volumes = [out[k] for k in WINDOWS.values() if out[k] is not None]
    out["on_hl"] = bool(out["birth_ms"] or any(v > 0 for v in volumes) or (out["total_value"] or 0) > 0)
    return out


def _value_at(life: list, ms: int) -> float | None:
    found = None
    for ts, value in life:
        if ts > ms:
            break
        found = value
    return found


def wake_events(previous: dict | None, current: dict | None, now_ms: int) -> list[dict]:
    """Events from one successful probe against the previous successful one."""
    if not current:
        return []
    value, birth = current.get("total_value"), current.get("birth_ms")
    detail = {"total_value": value, "week_volume": current.get("week_volume"),
              "month_volume": current.get("month_volume"), "birth_ms": birth}
    if previous is None:
        if current.get("on_hl") and birth and now_ms - birth <= RECENT_MS:
            return [{"kind": "hl_opened", "detail": detail}]
        if value is not None and value >= GROW_TO_USD:
            then = _value_at(current.get("life") or [], now_ms - RECENT_MS)
            if then is not None and then < GROW_FROM_BELOW_USD:
                return [{"kind": "hl_grew", "detail": {**detail, "from_usd": then}}]
        return []
    out = []
    opened = previous.get("on_hl") is False and bool(current.get("on_hl"))
    if opened:
        out.append({"kind": "hl_opened", "detail": detail})
    month = previous.get("month_volume")
    if (previous.get("on_hl") is True and isinstance(month, (int, float)) and not isinstance(month, bool)
            and month == 0.0 and (current.get("week_volume") or 0) > 0):
        out.append({"kind": "hl_woke", "detail": detail})
    before = previous.get("total_value")
    if isinstance(before, (int, float)) and not isinstance(before, bool) and value is not None:
        if not opened and before < GROW_FROM_BELOW_USD and value >= GROW_TO_USD:
            out.append({"kind": "hl_grew", "detail": {**detail, "from_usd": before}})
        if before >= EMPTY_FROM_USD and value < EMPTY_BELOW_USD:
            out.append({"kind": "hl_emptied", "detail": {**detail, "from_usd": before}})
    return out


def _thin_probes(probes: list) -> list:
    if len(probes) <= MAX_PROBES:
        return probes
    older, recent = probes[:-MAX_PROBES], probes[-MAX_PROBES:]
    monthly: dict[str, list] = {}
    for row in older:
        monthly.setdefault(str(row[0])[:7], row)
    return sorted(monthly.values(), key=lambda r: r[0]) + recent


def apply_probe(case: dict, reading: dict | None, *, now_ms: int, error: str | None = None) -> list[dict]:
    """Record one probe on the case. A failed probe records only that it failed
    (rule 5): the vitals, life and history stay as the last good read left them."""
    hl = case.setdefault("hl", {})
    hl["probed_at"] = iso(now_ms)
    if reading is None:
        hl["probe_ok"] = False
        hl["probe_error"] = str(error or "unreadable portfolio")[:200]
        return []
    previous = ({k: hl.get(k) for k in ("on_hl", "total_value", "month_volume", "week_volume")}
                if hl.get("last_ok_at") else None)
    found = wake_events(previous, reading, now_ms)
    hl["probe_ok"] = True
    hl.pop("probe_error", None)
    for key in FIELDS:
        hl[key] = reading.get(key)
    hl["life"] = reading.get("life") or []
    hl["last_ok_at"] = iso(now_ms)
    probes = list(hl.get("probes") or [])
    row = [day(now_ms), reading.get("total_value"), reading.get("month_volume"), reading.get("day_volume")]
    if probes and probes[-1][0] == row[0]:
        probes[-1] = row
    else:
        probes.append(row)
    hl["probes"] = _thin_probes(probes)
    return found
