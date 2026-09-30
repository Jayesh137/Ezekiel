#!/usr/bin/env python3
"""Report a workflow's outcome: open, re-ping or close its failure issue.

Measured 2026-09-30: trace.yml failed on 34 of 36 runs over ~22 hours and
nobody noticed. The failure step opened "[pipeline] trace workflow failing"
once (#35, 2026-09-27) and then did NOTHING on every later failure, because an
issue already existed — and an issue is not a channel anyone watches. The
recovery never closed it either, so an issue left open from an old, fixed
failure silenced the next, unrelated one. A failing pipeline is our capability,
not something about him, so it routes as HIGH — but it has to route.

So, per workflow:
  * failure, no open issue       -> open one and push HIGH to ntfy;
  * failure, issue open          -> comment + push again once REPING_HOURS have
                                    passed since the last push, so a pipeline
                                    that stays red keeps saying so;
  * success, issue open          -> close it, which re-arms the alert.

Standard library only: this runs from `if: failure()` steps, including when
`pip install` is the step that failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

REPING_HOURS = 6
MARKER = "<!-- pipeline-status:pinged -->"


def decide(outcome: str, issue: dict | None, last_ping: datetime | None, now: datetime) -> str:
    """One of: open, reping, quiet, close, none. Pure; unit-tested."""
    if outcome == "success":
        return "close" if issue else "none"
    if issue is None:
        return "open"
    if last_ping is None or now - last_ping >= timedelta(hours=REPING_HOURS):
        return "reping"
    return "quiet"


def _api(method: str, path: str, token: str, body: dict | None = None):
    url = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def find_issue(title: str, token: str) -> dict | None:
    for page in range(1, 6):
        rows = _api("GET", f"/issues?state=open&per_page=100&page={page}", token) or []
        for row in rows:
            if row.get("title") == title and "pull_request" not in row:
                return row
        if len(rows) < 100:
            return None
    return None


def last_ping(issue: dict, token: str) -> datetime | None:
    """The newest push we made: a marked comment, else the issue's creation.

    An issue opened before this script existed carries no marker, and its
    creation date then stands in — so an old silent issue re-pings at once.
    """
    comments = _api("GET", f"/issues/{issue['number']}/comments?per_page=100", token) or []
    marked = [_when(c["created_at"]) for c in comments if MARKER in (c.get("body") or "")]
    if marked:
        return max(marked)
    return _when(issue["created_at"]) if MARKER in (issue.get("body") or "") else None


def ntfy(subject: str, body: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("[pipeline-status] NTFY_TOPIC unset - issue only")
        return
    req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body.encode(), method="POST",
                                 headers={"Title": subject, "Priority": "high"})
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except (urllib.error.URLError, OSError) as exc:
        print(f"[pipeline-status] ntfy send failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--title", required=True, help="the failure issue's exact title")
    ap.add_argument("--outcome", choices=("failure", "success"), required=True)
    ap.add_argument("--note", default="", help="extra context for the issue body")
    args = ap.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    subject = f"[EZEKIEL] HIGH: {args.title.removeprefix('[pipeline] ')}"
    try:
        issue = find_issue(args.title, token)
        ping = last_ping(issue, token) if issue and args.outcome == "failure" else None
        action = decide(args.outcome, issue, ping, datetime.now(UTC))
        print(f"[pipeline-status] {args.title}: {args.outcome} -> {action}")
        body = f"Failing run: {run_url}\n\n{args.note}".strip()
        if action == "open":
            _api("POST", "/issues", token, {"title": args.title, "body": f"{body}\n\n{MARKER}"})
            ntfy(subject, body)
        elif action == "reping":
            _api("POST", f"/issues/{issue['number']}/comments", token,
                 {"body": f"Still failing: {run_url}\n\n{MARKER}"})
            ntfy(subject, f"Still failing: {run_url}")
        elif action == "close":
            _api("POST", f"/issues/{issue['number']}/comments", token,
                 {"body": f"Recovered: {run_url}"})
            _api("PATCH", f"/issues/{issue['number']}", token, {"state": "closed"})
    except Exception as exc:  # noqa: BLE001 - reporting must never be what fails a run
        print(f"[pipeline-status] could not report ({type(exc).__name__}: {exc})")
        if args.outcome == "failure":
            ntfy(subject, f"Failing run (issue update failed): {run_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
