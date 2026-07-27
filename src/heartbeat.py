# src/heartbeat.py
"""Watches the watcher: alerts when data collection has gone stale.

The system was silently dead for 21 days (last automated commit 2026-07-06)
because nothing monitored the collector itself. check_silence() in collector.py
detects a silent *trader*, but it only runs if the collector is running — the one
failure mode it cannot cover is its own.

This runs on its own schedule and only reads committed data, so it stays green
even when every collection job is failing.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, read_cursor, write_cursor, now_ms

# Collection runs every 15 min; alert once it has missed ~8 consecutive cycles.
STALE_AFTER_MINUTES = 120
# Don't re-alert more than once a day while an outage persists.
ALERT_COOLDOWN_HOURS = 24


def data_age_minutes(index_path: Path | None = None, now: datetime | None = None) -> float | None:
    """Minutes since data/index.json was last updated. None if unreadable.

    Pure enough to test: both the path and the clock are injectable.
    """
    path = index_path or (DATA_DIR / "index.json")
    if not path.exists():
        return None
    try:
        with open(path) as f:
            last = json.load(f).get("last_updated")
        if not last:
            return None
        ts = datetime.fromisoformat(last)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, TypeError):
        return None
    reference = now or datetime.now(timezone.utc)
    return (reference - ts).total_seconds() / 60.0


def is_stale(age_minutes: float | None, threshold: float = STALE_AFTER_MINUTES) -> bool:
    """Unreadable/missing index counts as stale — absence of evidence is the
    failure we're looking for here, not a reason to stay quiet."""
    if age_minutes is None:
        return True
    return age_minutes > threshold


def check_freshness() -> dict:
    """Alert if collection has stalled. Returns a status record."""
    from src.alerts import alert_collection_stale

    age = data_age_minutes()
    stale = is_stale(age)
    status = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "data_age_minutes": round(age, 1) if age is not None else None,
        "threshold_minutes": STALE_AFTER_MINUTES,
        "stale": stale,
    }

    if not stale:
        print(f"[heartbeat] OK — data is {age:.0f} min old (threshold {STALE_AFTER_MINUTES})")
        write_cursor("heartbeat_alerted_ms", 0)
        return status

    last_alert = read_cursor("heartbeat_alerted_ms")
    if last_alert and (now_ms() - last_alert) < ALERT_COOLDOWN_HOURS * 3600 * 1000:
        print(f"[heartbeat] STALE ({age}) but within alert cooldown — not re-alerting")
        status["alerted"] = False
        return status

    age_desc = f"{age:.0f} minutes" if age is not None else "unknown (index unreadable)"
    print(f"[heartbeat] STALE: data age {age_desc} exceeds {STALE_AFTER_MINUTES} min")
    if alert_collection_stale(age, STALE_AFTER_MINUTES):
        write_cursor("heartbeat_alerted_ms", now_ms())
        status["alerted"] = True
    else:
        status["alerted"] = False
    return status


def main():
    status = check_freshness()
    # Non-zero exit makes the workflow red too, so the outage is visible in the
    # Actions tab even if email delivery is broken.
    if status["stale"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
