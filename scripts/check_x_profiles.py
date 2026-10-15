#!/usr/bin/env python3
"""Watch his X accounts and send ONE notice when anything on them changes.

`config.x_accounts` pins each account by numeric id. Per run, per account: the
profile from api.fxtwitter.com (every field), falling back to api.vxtwitter.com;
when an account reads public, its timeline too. `src/x_watch.py` diffs against
`data/x_watch/latest.json` (watch.yml is the single writer). The notice goes
through `alerts.send_notice` at high priority — the owner asked to be told —
and never through `send_alert`: this identifies no wallet, so it touches no
severity gate and no delivery health. `X_NOTICES=0` withholds it.

A mirror outage is unreadable, never "nothing changed": the stored profile
stands, and `src/feed_health.py` reports the feed blind once every account has
been unreadable for six hours. The step never fails on an outage.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import x_watch
from src.utils import DATA_DIR, atomic_write_json, load_config

STATE = DATA_DIR / "x_watch" / "latest.json"
FX = "https://api.fxtwitter.com"
VX = "https://api.vxtwitter.com"
HEADERS = {"User-Agent": "Ezekiel-watch/1.0 (+https://github.com/Jayesh137/Ezekiel)",
           "Accept": "application/json"}


def get(url: str) -> tuple[int | None, object]:
    """(HTTP status, parsed JSON or None). Never raises; never follows a
    redirect, because fxtwitter answers an unknown handle with a 302."""
    import requests
    try:
        r = requests.get(url, headers=HEADERS, timeout=(10, 30), allow_redirects=False)
    except Exception as exc:                                  # noqa: BLE001 - transport
        print(f"[x_watch] {url} failed ({type(exc).__name__}): {exc}")
        return None, None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


# fx answers a spurious 404 on about one read in four (measured 2026-10-15), so
# it is asked again before the fallback mirror, which lacks likes and media.
FX_ATTEMPTS = 3


def read_profile(handle: str) -> dict:
    for _ in range(FX_ATTEMPTS):
        fx = x_watch.parse_fx(*get(f"{FX}/{handle}"))
        if fx[0] == "ok":
            return x_watch.pick(fx, ("unreadable", None))
    vx = x_watch.parse_vx(*get(f"{VX}/{handle}"))
    return x_watch.pick(fx, vx)


def read_timeline(handle: str) -> list[dict] | None:
    _, doc = get(f"{FX}/2/profile/{handle}/statuses?count=20")
    return x_watch.parse_timeline(doc)


def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    accounts = (load_config().get("x_accounts") or {})
    if not accounts:
        print("[x_watch] no x_accounts configured")
        return 0
    previous = load_state()
    stored_accounts = previous.get("accounts") or {}
    now = datetime.now(UTC)
    out_accounts, changes = {}, []
    for account_id, configured in accounts.items():
        stored = stored_accounts.get(account_id)
        handle = (stored or {}).get("handle") or configured
        reading = read_profile(handle)
        state, delta = x_watch.apply(account_id, handle, stored, reading, now)
        profile = state.get("profile") or {}
        if reading["status"] == "ok" and profile.get("protected") is False:
            state, posts = x_watch.new_posts(account_id, handle, state, read_timeline(handle), now)
            delta += posts
        out_accounts[account_id] = state
        changes += delta
        print(f"[x_watch] @{state.get('handle')}: {reading['status']}"
              f"{' via ' + reading['source'] if reading.get('source') else ''}"
              f"{', baseline' if stored is None and reading['status'] == 'ok' else ''}"
              f" — tweets {profile.get('tweets')}, likes {profile.get('likes')}, "
              f"following {profile.get('following')}, followers {profile.get('followers')}, "
              f"protected {profile.get('protected')}; {len(delta)} change(s)")
    for c in changes:
        # The dashboard and phone show this line, so they read exactly as the notice.
        c["line"] = x_watch.describe(c)
    history = x_watch.trim((previous.get("changes") or []) + changes)
    doc = {
        "computed_at": now.isoformat(),
        "accounts": out_accounts,
        "accounts_watched": len(out_accounts),
        "unreadable": [i for i, a in out_accounts.items() if a.get("last_read") == "unreadable"],
        "changes": history,
        "last_change_at": changes[-1]["at"] if changes else previous.get("last_change_at"),
    }
    atomic_write_json(STATE, doc)
    for c in changes:
        print(f"[x_watch]   CHANGE {x_watch.describe(c)}")
    rendered = x_watch.render(changes)
    if rendered is None:
        return 0
    if os.environ.get("X_NOTICES", "1") == "0":
        print("[x_watch] notice withheld (X_NOTICES=0)")
        return 0
    from src.alerts import send_notice
    handles = sorted({c["handle"] for c in changes})
    click = f"https://x.com/{handles[0]}" if len(handles) == 1 else None
    via = send_notice(rendered[0], rendered[1], priority="high", click=click)
    print(f"[x_watch] notice {'sent via ' + ', '.join(via) if via else 'not delivered'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
