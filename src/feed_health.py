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

# A FRESH file can still be blind: a detector whose every read fails, or whose
# parser no longer understands an endpoint, keeps writing `computed_at` on
# time and reports nothing, which is the staleness check's blind spot. One bad
# run heals itself on the next, so a blind reading is reported only once it
# has lasted this long; an endpoint, key or parser broken for six hours is not
# coming back by itself.
BLIND_HOURS = 6.0
# Below this many wallets a detector's zero is not surprising enough to act on.
MIN_WALLETS = 20
# ~50 minutes of HyperEVM blocks. Circle transfers into and out of Hyperliquid
# ran at 26-45 per ~2,400 blocks when this was written, so a read this long
# with none at all is a decoder or a feed that has stopped understanding.
CIRCLE_MIN_BLOCKS_FOR_ZERO = 3_000


def _unreadable_share(doc: dict, checked_key: str, unreadable_key: str = "unreadable"):
    checked = doc.get(checked_key)
    unreadable = doc.get(unreadable_key)
    if isinstance(unreadable, list):
        unreadable = len(unreadable)
    if not isinstance(checked, int) or checked < MIN_WALLETS or not isinstance(unreadable, int):
        return None
    if unreadable * 2 >= checked:
        return f"{unreadable} of {checked} reads failed"
    return None


def _agents(doc: dict):
    failed = _unreadable_share(doc, "wallets_checked")
    if failed:
        return failed
    if (doc.get("wallets_checked") or 0) >= MIN_WALLETS and doc.get("agents_seen") == 0:
        return f"0 agents across {doc['wallets_checked']} wallets (214 when this was written)"
    return None


def _surface(doc: dict):
    failed = _unreadable_share(doc, "wallets_checked")
    if failed:
        return failed
    if (doc.get("wallets_checked") or 0) >= MIN_WALLETS and doc.get("subaccounts") == {}:
        return (f"0 sub-accounts across {doc['wallets_checked']} wallets "
                f"(56 when this was written)")
    return None


def _identities(doc: dict):
    return _unreadable_share(doc, "known")


def _watch(doc: dict):
    watched, unreadable = doc.get("watched"), doc.get("unreadable")
    if isinstance(watched, int) and watched > 0 and isinstance(unreadable, list) \
            and len(unreadable) >= watched:
        return f"every one of {watched} watched wallet(s) unreadable"
    return None


def _circle(doc: dict):
    if doc.get("error"):
        return f"reads failing: {str(doc['error'])[:120]}"
    blocks = doc.get("blocks_read")
    if isinstance(blocks, int) and blocks >= CIRCLE_MIN_BLOCKS_FOR_ZERO and \
            (doc.get("deposits_read") or 0) + (doc.get("withdrawals_read") or 0) == 0:
        return f"0 Circle transfers decoded in {blocks:,} blocks"
    return None


def _scan(doc: dict):
    return "scanned 0 wallets" if doc.get("wallets_scanned") == 0 else None


def _correlation(doc: dict):
    return "0 candidate deposits considered" if doc.get("candidates_considered") == 0 else None


def _roster(doc: dict):
    return "the roster holds no wallets" if doc.get("wallet_count") == 0 else None


def _dormancy(doc: dict):
    return "0 candidates scored" if doc.get("candidates_scored") == 0 else None


# feed name -> a function answering "is this fresh reading blind?" with a reason.
BLIND_CHECKS = {
    "close watch": _watch,
    "Circle flows": _circle,
    "roster": _roster,
    "HL account surface": _surface,
    "identities": _identities,
    "shared agents": _agents,
    "dormancy handoff": _dormancy,
    "behavioural scan": _scan,
    "amount correlation": _correlation,
}


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


def blind(feeds: dict, data_dir: Path, since: dict | None,
          now: datetime | None = None) -> tuple[list[dict], dict]:
    """Fresh readings that have been blind for `BLIND_HOURS`. Pure given the files.

    `since` maps feed -> when its blindness was first seen, as returned by the
    previous run; the updated map is returned for the caller to store. A feed
    that reads normally again is dropped from it, so the clock restarts.
    """
    import json

    now = now or datetime.now(UTC)
    since = dict(since or {})
    problems, still = [], {}
    for name, (rel, _key, _limit) in feeds.items():
        check = BLIND_CHECKS.get(name)
        if check is None:
            continue
        try:
            with open(Path(data_dir) / rel) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue          # assess() reports a missing or unreadable file
        reason = check(doc) if isinstance(doc, dict) else None
        if not reason:
            continue
        first = since.get(name) or now.isoformat()
        still[name] = first
        hours = (_age_minutes(first, now) or 0.0) / 60.0
        if hours >= BLIND_HOURS:
            problems.append({"feed": name, "path": rel,
                             "problem": f"writing on time but blind for {hours:.1f}h: {reason}"})
    return problems, still
