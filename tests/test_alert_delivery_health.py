# tests/test_alert_delivery_health.py
"""Alert delivery health must be visible in the data, not only in a job log.

Found on 2026-08-12 by auditing what the system had actually delivered:

  * 25 candidates have been promoted to ALERT across the scan history;
  * not one alert cursor has ever been committed, and those are written only
    after a successful send — so not one was ever delivered;
  * the SMTP Delivery Check workflow has run exactly once, on 2026-07-27, and
    failed;
  * every scan.yml run since has reported success, because a failed send returns
    False and logs, without failing the job.

The project had already been bitten by this once: tests/test_alert_delivery_state
records a MIGRATION_CANDIDATE whose send failed with 535 and was silently
retired. That fix made undelivered discoveries retryable. It did not make the
outage VISIBLE, so a detection system with a dead output channel looks identical
to a quiet week — from the dashboard, from the data, and from the Actions tab.

Email cannot report its own failure. The signal has to land somewhere the
operator already looks, which is the repository data the dashboard reads.
"""

import json

import pytest

from src import alerts


def test_the_suite_cannot_write_to_the_real_data_directory():
    """The guard that stops this file's own mechanism corrupting production.

    _record_delivery writes on every send attempt, and ten existing tests
    exercise send_alert without redirecting DATA_DIR. Running the suite once
    filled the real data/alerts/latest.json with fixture rows — subjects "s",
    "s1", "first" — and it reached a commit. Pushed, the dashboard would have
    announced ALERTING IS DOWN on the strength of unit-test data, which is a
    worse failure than having no monitor at all.

    conftest redirects DATA_DIR for every test. This asserts the redirect is
    actually in force, so the protection cannot silently lapse.
    """
    from src import utils
    from tests.conftest import REAL_DATA_DIR

    assert alerts.DATA_DIR != REAL_DATA_DIR, "alerts.DATA_DIR must be sandboxed"
    assert utils.DATA_DIR != REAL_DATA_DIR, "utils.DATA_DIR must be sandboxed"
    assert "data" in str(alerts.DATA_DIR)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Alerting pointed at a temp data dir, with credentials present."""
    monkeypatch.setattr(alerts, "DATA_DIR", tmp_path)
    store = {}
    monkeypatch.setattr(alerts, "read_cursor", lambda n: store.get(n, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setenv("BREVO_SMTP_LOGIN", "id@smtp-brevo.example")
    monkeypatch.setenv("BREVO_SMTP_KEY", "a-key")
    monkeypatch.setenv("ALERT_EMAIL", "op@example.com")
    return tmp_path


def health(root):
    p = root / "alerts" / "latest.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_a_failed_send_is_recorded_as_unhealthy(wired, monkeypatch):
    """The whole point: a dead channel must be legible from the data."""
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)

    assert alerts.send_alert("[EZEKIEL] CRITICAL: test", "body") is False

    h = health(wired)
    assert h is not None, "a failed send must leave a record"
    assert h["healthy"] is False
    assert h["consecutive_failures"] == 1
    assert h["last_failure_at"]
    assert h["last_success_at"] is None
    assert "connection refused" in h["last_failure_reason"]


def test_repeated_failures_accumulate(wired, monkeypatch):
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    for _ in range(3):
        monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
        alerts.send_alert("s", "b")
    h = health(wired)
    assert h["consecutive_failures"] == 3
    assert h["undelivered"] == 3


def test_a_successful_send_clears_the_alarm(wired, monkeypatch):
    class OK:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, *a): pass
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    alerts.send_alert("s", "b")
    assert health(wired)["healthy"] is False

    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda *a, **k: OK())
    assert alerts.send_alert("s2", "b2") is True

    h = health(wired)
    assert h["healthy"] is True
    assert h["consecutive_failures"] == 0
    assert h["undelivered"] == 0
    assert h["last_success_at"]


def test_missing_credentials_are_recorded_without_leaking_them(wired, monkeypatch):
    """Unconfigured is a delivery outage too — and the record must name the
    variable, never its value, exactly as the log does."""
    monkeypatch.delenv("BREVO_SMTP_KEY", raising=False)
    assert alerts.send_alert("s", "b") is False
    h = health(wired)
    assert h["healthy"] is False
    assert "BREVO_SMTP_KEY" in h["last_failure_reason"]
    assert "a-key" not in json.dumps(h), "a secret value must never be recorded"


def test_an_auth_rejection_is_recorded_by_code_not_credential(wired, monkeypatch):
    """535 is what actually happened here on 2026-07-27."""
    import smtplib as s
    def refuse(*a, **k):
        raise s.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
    class Boom:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): refuse()
        def sendmail(self, *a): pass
    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda *a, **k: Boom())
    assert alerts.send_alert("s", "b") is False
    h = health(wired)
    assert h["healthy"] is False
    assert "535" in h["last_failure_reason"]
    assert "a-key" not in json.dumps(h)


def test_recording_never_breaks_alerting(wired, monkeypatch):
    """If the health file cannot be written, sending must still work. A
    diagnostic that can take down the thing it diagnoses is worse than none."""
    class OK:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def sendmail(self, *a): pass
    monkeypatch.setattr(alerts.smtplib, "SMTP", lambda *a, **k: OK())
    monkeypatch.setattr(alerts, "save_latest",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))
    assert alerts.send_alert("s", "b") is True
