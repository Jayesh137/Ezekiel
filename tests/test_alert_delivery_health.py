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
    # Email is the only channel here unless a test says otherwise. Cleared
    # rather than assumed absent: an operator's own NTFY_TOPIC exported in the
    # shell would otherwise make these tests POST to ntfy.sh for real, which
    # is both a network call in a suite that must have none and a result that
    # depends on whose machine it runs on.
    for var in ("NTFY_TOPIC", "NTFY_INCLUDE_INFO",
                "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
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


# --- INFO suppression is policy, not an outage ---------------------------------
#
# Measured on production 2026-09-11. `_send_webhooks` deliberately refuses to
# buzz a phone for INFO — the first trace run after ntfy went live pushed 24
# "Operational Counterparty" notices at 3-11% confidence in one minute — and
# Brevo has never delivered anything. So every INFO alert reached no channel,
# and every one of them was recorded as a delivery FAILURE: six consecutive
# failures, `healthy: false`, and the dashboard announcing ALERTING IS DOWN
# while four CRITICALs that same afternoon were delivered by ntfy in seconds.
#
# That inverts the monitor. The question it exists to answer is "would I be
# told if the trader migrated?", and the answer was yes throughout. A flag
# pinned to false by a policy we chose on purpose cannot report the outage it
# was built for, because there is no state left for a real outage to change.
#
# So health is judged on the alerts that were MEANT to reach the operator.
# An INFO alert nothing carried is suppressed: counted, kept in `recent`, and
# never allowed to touch `healthy`. CRITICAL and HIGH — and any subject whose
# severity cannot be read, which must never be assumed harmless — are
# health-bearing exactly as before.


def test_an_info_alert_no_channel_carries_is_suppressed_not_failed(wired, monkeypatch):
    """The production case: INFO is not routed anywhere, so nothing failed."""
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))

    alerts.send_alert("[EZEKIEL] INFO: Operational Counterparty (33% confidence)", "body")

    h = health(wired)
    assert h is not None, "a suppressed alert must still leave a record"
    assert h["healthy"] is True, "INFO reaching no channel is policy, not an outage"
    assert h["consecutive_failures"] == 0
    assert h["undelivered"] == 0


def test_a_suppressed_info_alert_is_still_counted_and_visible(wired, monkeypatch):
    """Suppressed is not silent. It must be countable, or the policy hides
    how much the operator is no longer being told."""
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))

    alerts.send_alert("[EZEKIEL] INFO: first", "b")
    alerts.send_alert("[EZEKIEL] INFO: second", "b")

    h = health(wired)
    assert h["suppressed"] == 2
    assert h["recent"][-1]["status"] == "suppressed"
    assert h["recent"][-1]["subject"] == "[EZEKIEL] INFO: second"


def test_suppressing_info_does_not_clear_a_standing_failure(wired, monkeypatch):
    """The failure mode to avoid in the other direction: a stream of INFO
    notices must not wash a real CRITICAL outage out of the record."""
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))

    alerts.send_alert("[EZEKIEL] CRITICAL: migration candidate", "b")
    failed = health(wired)
    assert failed["healthy"] is False and failed["consecutive_failures"] == 1

    alerts.send_alert("[EZEKIEL] INFO: Operational Counterparty (3% confidence)", "b")

    h = health(wired)
    assert h["healthy"] is False, "a suppressed INFO must not mark the channel healthy"
    assert h["consecutive_failures"] == 1, "counters belong to health-bearing alerts"
    assert h["undelivered"] == 1
    assert "connection refused" in h["last_failure_reason"]
    assert h["last_failure_at"] == failed["last_failure_at"]


def test_info_counts_against_health_when_the_operator_opts_in(wired, monkeypatch):
    """NTFY_INCLUDE_INFO routes INFO to the instant channels. Once an alert is
    meant to arrive, failing to deliver it is an outage again."""
    monkeypatch.setenv("NTFY_INCLUDE_INFO", "1")
    monkeypatch.setattr(alerts.smtplib, "SMTP",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))

    alerts.send_alert("[EZEKIEL] INFO: Operational Counterparty (33% confidence)", "b")

    h = health(wired)
    assert h["healthy"] is False
    assert h["consecutive_failures"] == 1
