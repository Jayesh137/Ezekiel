# src/dormancy.py
"""The clearest migration signature there is: one wallet goes quiet, another starts.

Every other vector needs a connection — a transfer, a shared address, an
authorised agent, a matching amount. This one needs none. If he abandons a wallet
and funds a fresh one from somewhere we cannot see, the only thing left tying
them together is that the silence of one lines up with the birth of the other.

Two uses, and they are different questions:

  * A LIVE tripwire. Is he unusually quiet *right now*, measured against his own
    rhythm rather than a guessed constant? Then anything newly active deserves a
    hard look.
  * A RETROSPECTIVE score. Did a candidate's very first activity land inside an
    unusually long gap in his history?

Both are calibrated on the target's own gap distribution, because "quiet" means
nothing in the abstract. Measured 2026-09-10 across 75 active days: median gap
2 days, p90 5, p95 10, longest ever 21. A four-day silence is ordinary for him
and a fixed "dormant after 7 days" rule would cry wolf constantly.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

DORMANCY_DIR = DATA_DIR / "dormancy"

MS_PER_DAY = 86_400_000

# A gap must clear this percentile of the target's OWN gaps to count as
# anomalous. Below it, a candidate starting during a gap says nothing: he is
# quiet for a day or two constantly.
ANOMALOUS_GAP_PERCENTILE = 90

# How long after an anomalous gap opens a candidate may start and still count as
# a handoff. Beyond this the two events are merely in the same month.
HANDOFF_WINDOW_DAYS = 3


# A handoff alone is common. Measured 2026-11-14 over 397 random venue takers,
# 299 of them born inside his history: 27 (9.0%) scored as a handoff by chance,
# and every one of them paged CRITICAL, down to accounts holding nothing. What
# chance does not produce is a handoff WITH capital: 2 of 299 had ever held
# $100K and 0 of 299 had ever held $1M (Wilson ceiling 1.3%) — and capital
# arriving is what a migration of his account would look like. So the severity
# is the size: CRITICAL from $1M, HIGH from $100K, and below that the handoff
# is recorded (roster, casebook) without paging. A wallet whose value could not
# be read is HIGH: unmeasured is never assumed harmless.
HANDOFF_CRITICAL_USD = 1_000_000.0
HANDOFF_HIGH_USD = 100_000.0


def capital(portfolio) -> dict:
    """{'value', 'peak_value'} from one `portfolio` answer; None where unreadable."""
    peak = value = newest = None
    for item in portfolio if isinstance(portfolio, list) else []:
        if not (isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], dict)):
            continue
        for point in item[1].get("accountValueHistory") or []:
            try:
                ts, amount = int(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
            if amount != amount:            # NaN
                continue
            peak = amount if peak is None else max(peak, amount)
            if newest is None or ts > newest:
                newest, value = ts, amount
    return {"value": value, "peak_value": peak}


def handoff_severity(detail: dict) -> str:
    """CRITICAL / HIGH / INFO for a handoff, by the capital the wallet has held."""
    peak = (detail or {}).get("peak_value")
    if isinstance(peak, bool) or not isinstance(peak, (int, float)):
        return "HIGH"
    if peak >= HANDOFF_CRITICAL_USD:
        return "CRITICAL"
    return "HIGH" if peak >= HANDOFF_HIGH_USD else "INFO"


def operator_birth(wallet: str, birth_ms: int, fetch) -> dict:
    """{'birth_ms', 'master'}: the birth a handoff must be judged on.

    A sub-account is opened by its master, so its own birth says only when an
    existing operator added an account. Two of the four best-funded handoff
    leads on 2026-11-14 (`0xeaa5ed54…`, `0xa6727cd1…`) were sub-accounts of
    firms that long predate the silence they were "born" in. For a
    sub-account the operator's birth is its master's.

    `fetch` answers in `utils.hl_read`'s shape. Strict: an unreadable role or
    master raises ValueError, so the wallet is retried rather than judged on a
    birth that may not be its operator's (rule 5).
    """
    from src.hl_identity import parse_birth
    answer = fetch({"type": "userRole", "user": wallet})
    role = answer.get("data") if isinstance(answer, dict) and answer.get("ok") else None
    if not isinstance(role, dict) or not isinstance(role.get("role"), str):
        raise ValueError((answer or {}).get("error") or "role unreadable")
    if role["role"] != "subAccount":
        return {"birth_ms": birth_ms, "master": None}
    master = str(((role.get("data") or {}).get("master")) or "").lower()
    if not master.startswith("0x"):
        raise ValueError("sub-account without a readable master")
    answer = fetch({"type": "portfolio", "user": master})
    born = parse_birth(answer.get("data")) if isinstance(answer, dict) and answer.get("ok") else None
    if not born:
        raise ValueError("master birth unreadable")
    return {"birth_ms": min(int(birth_ms), int(born)), "master": master}


def active_days(fills) -> list[int]:
    """Sorted whole days on which anything traded."""
    days = set()
    for f in fills or []:
        ts = f.get("time") if isinstance(f, dict) else None
        try:
            days.add(int(ts) // MS_PER_DAY)
        except (TypeError, ValueError):
            continue
    return sorted(days)


def _percentile(values: list, pct: float):
    if not values:
        return None
    ordered = sorted(values)
    import math
    k = int(math.ceil(pct / 100.0 * len(ordered))) - 1
    return ordered[max(0, min(len(ordered) - 1, k))]


def gap_stats(days: list[int]) -> dict:
    """The target's own rhythm. Everything else is measured against this."""
    gaps = [b - a for a, b in zip(days, days[1:], strict=False)] if len(days) > 1 else []
    return {
        "active_days": len(days),
        "gaps": len(gaps),
        "median": _percentile(gaps, 50),
        "p90": _percentile(gaps, 90),
        "p95": _percentile(gaps, 95),
        "max": max(gaps) if gaps else None,
    }


def dormancy_state(days: list[int], now_day: int) -> dict:
    """Is he unusually quiet right now, judged against his own history?

    `unprecedented` is the one that matters: a silence longer than any he has
    ever taken is not a quiet week, it is a change of behaviour.
    """
    stats = gap_stats(days)
    if not days:
        return {"silent_days": None, "unusual": False, "unprecedented": False,
                "stats": stats, "reason": "no activity recorded — cannot judge"}
    silent = max(0, now_day - days[-1])
    threshold = stats["p90"]
    longest = stats["max"]
    return {
        "silent_days": silent,
        "last_active_day": days[-1],
        # Compared against HIS distribution. A fixed "dormant after 7 days" rule
        # would fire constantly on a trader whose median gap is 2 days and who
        # has taken 21 off before.
        "unusual": bool(threshold is not None and silent > threshold),
        "unprecedented": bool(longest is not None and silent > longest),
        "stats": stats,
    }


def anomalous_gaps(days: list[int],
                   percentile: float = ANOMALOUS_GAP_PERCENTILE) -> list[dict]:
    """Gaps longer than the target's own `percentile` — the windows worth watching."""
    if len(days) < 2:
        return []
    pairs = list(zip(days, days[1:], strict=False))
    gaps = [b - a for a, b in pairs]
    if not gaps:
        return []

    # Leave-one-out: judge each gap against the percentile of the OTHERS. A
    # single long silence otherwise sets the very threshold it has to clear —
    # with gaps [2,2,2,2,2,2,28] the p90 IS 28, so `> p90` excluded the only
    # anomaly there was. Barely changes anything on the real 74-gap history and
    # makes the rule correct on short ones.
    out = []
    for i, (a, b) in enumerate(pairs):
        others = gaps[:i] + gaps[i + 1:]
        floor = _percentile(others, percentile)
        if floor is not None and (b - a) > floor:
            out.append({"start_day": a, "end_day": b, "length": b - a})
    return out


def handoff_score(target_days: list[int], candidate_days: list[int],
                  window: int = HANDOFF_WINDOW_DAYS,
                  percentile: float = ANOMALOUS_GAP_PERCENTILE) -> dict:
    """Did this candidate come alive while the target was unusually quiet?

    Scores only the candidate's FIRST active day. A wallet already trading before
    the silence began is not a handoff — it is a wallet that happens to exist,
    and treating it as one would match half the leaderboard.
    """
    if not target_days or not candidate_days:
        return {"score": 0.0, "reason": "no activity on one side"}
    first = candidate_days[0]
    if first <= target_days[0]:
        return {"score": 0.0, "reason": "candidate predates the target's history"}

    best = None
    for gap in anomalous_gaps(target_days, percentile):
        # Must begin inside the silence, not before it and not long after.
        if not (gap["start_day"] < first <= gap["start_day"] + window):
            continue
        delay = first - gap["start_day"]
        # Tighter is stronger, and a longer silence is a stronger backdrop.
        closeness = 1.0 - (delay - 1) / max(1, window)
        weight = min(1.0, gap["length"] / 21.0)
        score = round(max(0.0, closeness) * (0.5 + 0.5 * weight), 4)
        if best is None or score > best["score"]:
            best = {"score": score, "gap_start_day": gap["start_day"],
                    "gap_length": gap["length"], "delay_days": delay,
                    "candidate_first_day": first}
    if best is None:
        return {"score": 0.0,
                "reason": "candidate did not start during an unusual silence"}
    return best


def build_report(target_fills, candidates: dict, now_day: int | None = None) -> dict:
    """Live dormancy state plus a handoff score for every candidate."""
    if now_day is None:
        now_day = int(datetime.now(UTC).timestamp()) // 86_400
    t_days = active_days(target_fills)
    state = dormancy_state(t_days, now_day)
    scored = {}
    for wallet, fills in (candidates or {}).items():
        result = handoff_score(t_days, active_days(fills))
        if result.get("score", 0) > 0:
            scored[(wallet or "").lower()] = result
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "dormancy": state,
        "anomalous_gaps": anomalous_gaps(t_days),
        "handoffs": scored,
        "candidates_scored": len(candidates or {}),
    }


def save(report: dict) -> None:
    save_latest(str(DORMANCY_DIR), report)
