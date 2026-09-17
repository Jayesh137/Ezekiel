# src/feed_health.py
"""Is every detector still producing a reading? Blindness must not look like calm.

The failure this project has rediscovered most often is an absence: the
frontier dead for two days, a trace step killed on its timeout for hours, the
local dispatcher dead on battery, a Circle pool days behind — each presenting
as "nothing happening", indistinguishable from there being nothing to find.
`src/heartbeat.py` watches the collector's index and nothing else.

This reads each detector's own `latest.json` timestamp and reports any older
than its limit. Limits sit well above each feed's MEASURED cadence rather than
its cron (GitHub delivers crons a median of 198 minutes apart here; the local
dispatcher is far faster while the operator's machine is on), so a report means
an outage, not a slow afternoon. A file that cannot be read is reported too —
an unreadable reading is not a fresh one (rule 5).

Checked CROSSWISE: watch.yml checks the feeds trace, scan, analyze and the
collector write, and trace.yml checks the watch's, so a dead workflow is
reported by one that is alive, and both commit their alert cooldowns.
"""

from datetime import UTC, datetime
from pathlib import Path

# name -> (path under data/, timestamp key, max age in minutes)
WATCH_FEEDS = {
    "close watch": ("watchlist/latest.json", "computed_at", 360),
    "deposit-address sentinels": ("deposit_sentinels/latest.json", "computed_at", 360),
    "Circle flows": ("circle_flows/latest.json", "computed_at", 360),
}
OTHER_FEEDS = {
    "roster": ("roster/latest.json", "computed_at", 720),
    "HL account surface": ("hl_surface/latest.json", "computed_at", 720),
    "identities": ("identity/latest.json", "computed_at", 720),
    "shared agents": ("agent_links/latest.json", "computed_at", 720),
    "dormancy handoff": ("dormancy/latest.json", "computed_at", 720),
    "behavioural scan": ("scans/latest.json", "scan_time", 720),
    "amount correlation": ("correlations/latest.json", "computed_at", 2_160),
    "migration risk": ("risk/latest.json", "computed_at", 360),
}
GROUPS = {"watch": WATCH_FEEDS, "other": OTHER_FEEDS}

# The Circle-flow cursor can fall behind while its runs still succeed. ~8h of
# HyperEVM blocks.
CIRCLE_MAX_LAG_BLOCKS = 30_000


def _age_minutes(stamp, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (now - when).total_seconds() / 60.0


def assess(feeds: dict, data_dir: Path, now: datetime | None = None) -> list[dict]:
    """Every feed in `feeds` that is stale, missing or unreadable. Pure given the files."""
    import json

    now = now or datetime.now(UTC)
    problems = []
    for name, (rel, key, limit) in feeds.items():
        path = Path(data_dir) / rel
        try:
            with open(path) as f:
                doc = json.load(f)
        except FileNotFoundError:
            problems.append({"feed": name, "problem": "never written", "path": rel})
            continue
        except (OSError, ValueError) as exc:
            problems.append({"feed": name, "problem": f"unreadable: {type(exc).__name__}",
                             "path": rel})
            continue
        age = _age_minutes((doc or {}).get(key) if isinstance(doc, dict) else None, now)
        if age is None:
            problems.append({"feed": name, "problem": f"no readable {key}", "path": rel})
        elif age > limit:
            problems.append({"feed": name, "problem": f"last reading {age / 60:.1f}h ago "
                             f"(limit {limit / 60:.0f}h)", "path": rel,
                             "age_minutes": round(age, 1)})
        if name == "Circle flows" and isinstance(doc, dict):
            lag = doc.get("lag_blocks")
            if isinstance(lag, int) and lag > CIRCLE_MAX_LAG_BLOCKS:
                problems.append({"feed": name, "problem": f"cursor {lag:,} blocks behind "
                                 f"the chain head", "path": rel})
    return problems
