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
files in data/state/.

Tests that need their own data dir still patch it themselves; an explicit
monkeypatch inside a test applies after this fixture and wins.
"""

import pytest

from src import alerts, utils

REAL_DATA_DIR = utils.DATA_DIR


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

    Compares the alert-health file's mtime across the test. Cheap, and it fails
    loudly rather than leaving state to be discovered in a diff later.
    """
    probe = REAL_DATA_DIR / "alerts" / "latest.json"
    before = probe.stat().st_mtime_ns if probe.exists() else None
    yield
    after = probe.stat().st_mtime_ns if probe.exists() else None
    assert before == after, (
        f"a test wrote to the real {probe} — redirect DATA_DIR instead. "
        f"Production alert-delivery state must never come from a test run."
    )
