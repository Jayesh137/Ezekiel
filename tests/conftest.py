# tests/conftest.py
"""No test may write into the real data/ directory.

This exists because a test suite quietly wrote production state and it was
committed. src/alerts.py records every send attempt to data/alerts/latest.json
so the dashboard can show an ALERTING IS DOWN banner. Ten existing tests
exercise send_alert without redirecting DATA_DIR, so running the suite filled
that file with fixture rows — subjects "s", "s1", "first" — and it reached a
commit. Pushed, the dashboard would have announced "ALERTING IS DOWN, 4 alerts
not delivered" on the strength of unit-test data.

A false alarm from the alerting-health monitor is worse than no monitor, so the
guard is global rather than per-test: an eleventh test that forgets is otherwise
inevitable. The same class of leak had already happened once with the cursor
files in data/state/ — a warning this file carried before src/chain/collect.py
existed to make it literal: it writes data/state/transfer_cursors.json,
data/transfers_spam/latest.json and the whole data/transfers/ tree, none of
which utils.DATA_DIR patching alone protects, since collect.py derives its own
TRANSFERS_DIR/SPAM_DIR/CURSOR_PATH from DATA_DIR once at import time — a test
that forgets to patch those three module globals directly writes straight
through to the real paths regardless of what utils.DATA_DIR is set to.

Tests that need their own data dir still patch it themselves; an explicit
monkeypatch inside a test applies after this fixture and wins.
"""

from pathlib import Path

import pytest

from src import alerts, utils

REAL_DATA_DIR = utils.DATA_DIR

# label -> real path a leaking test must never touch.
_PROBES = {
    "alert-delivery state": REAL_DATA_DIR / "alerts" / "latest.json",
    "sweep cursor state": REAL_DATA_DIR / "state" / "transfer_cursors.json",
    "the spam rollup": REAL_DATA_DIR / "transfers_spam" / "latest.json",
    "collected transfer records": REAL_DATA_DIR / "transfers",
}


def _signature(path: Path):
    """Existence + mtime; for a directory, every entry beneath it, recursively.

    One level of children caught a new chain subdirectory being created, but
    `append_records` truncates and rewrites an existing `{date}.json` in place —
    that bumps the file's own mtime but neither its chain directory's nor
    `transfers/`'s, so once a chain directory and a dated file both exist for
    real, a test that appends into that same file on the same day went
    undetected. A full recursive walk catches a rewrite at any depth.
    """
    if not path.exists():
        return None
    if path.is_file():
        return path.stat().st_mtime_ns
    return {str(child.relative_to(path)): child.stat().st_mtime_ns
           for child in path.rglob("*")}


@pytest.fixture(autouse=True)
def _never_write_to_real_data(tmp_path, monkeypatch):
    """Point every module's DATA_DIR at a per-test temp directory."""
    sandbox = tmp_path / "data"
    sandbox.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(utils, "DATA_DIR", sandbox)
    monkeypatch.setattr(alerts, "DATA_DIR", sandbox)
    yield sandbox


@pytest.fixture(autouse=True)
def _fail_if_the_real_data_dir_was_touched():
    """Backstop: catch a module that captured DATA_DIR before we patched it.

    Compares a cheap existence+mtime signature across the test for every real
    path this suite has been caught writing to, or is positioned to write to.
    Fails loudly rather than leaving state to be discovered in a diff later.
    """
    before = {label: _signature(p) for label, p in _PROBES.items()}
    yield
    for label, p in _PROBES.items():
        assert before[label] == _signature(p), (
            f"a test wrote to the real {p} — redirect DATA_DIR instead. "
            f"Production {label} must never come from a test run."
        )
