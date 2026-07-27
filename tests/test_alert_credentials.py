# tests/test_alert_credentials.py
"""SMTP credential handling for Brevo.

A live Trace Fund Flows run reached "0/1 discovery alert(s) sent" with
535 5.7.8 Authentication failed. The cause was in the code, not the secrets:
send_alert authenticated as the literal username "apikey" — SendGrid's
convention — which Brevo always rejects. Brevo requires the SMTP login from
Settings -> SMTP & API as the username and an SMTP key as the password.

These tests pin the credential contract and prove no credential value is logged.
All SMTP is mocked; nothing here touches the network.
"""

import smtplib

import pytest

from src import alerts

LOGIN = "sentinel-login@smtp-brevo.example"
KEY = "sentinel-smtp-key"
EMAIL = "recipient@example.com"


class FakeSMTP:
    """Records what the caller does, so the credential contract is observable."""

    instances = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.login_args = None
        self.starttls_called = False
        self.sendmail_args = None
        self.entered = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def sendmail(self, from_addr, to_addrs, message):
        self.sendmail_args = (from_addr, to_addrs, message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setattr(alerts.smtplib, "SMTP", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("BREVO_SMTP_LOGIN", LOGIN)
    monkeypatch.setenv("BREVO_SMTP_KEY", KEY)
    monkeypatch.setenv("ALERT_EMAIL", EMAIL)


# --- the credential contract ------------------------------------------------------

def test_login_uses_smtp_login_and_smtp_key(smtp, creds):
    """The headline fix: username is BREVO_SMTP_LOGIN, password is BREVO_SMTP_KEY."""
    assert alerts.send_alert("subject", "body") is True
    server = smtp.instances[0]
    assert server.login_args == (LOGIN, KEY)


def test_login_username_is_not_alert_email(smtp, creds):
    """The Brevo account email is not a valid SMTP username."""
    server_user, _ = (alerts.send_alert("s", "b"), smtp.instances[0].login_args)[1]
    assert server_user != EMAIL
    assert server_user == LOGIN


def test_login_username_is_never_the_literal_apikey(smtp, creds):
    """Regression guard for the exact defect: "apikey" is SendGrid's convention
    and Brevo answers it with 535 5.7.8 Authentication failed."""
    alerts.send_alert("s", "b")
    user, password = smtp.instances[0].login_args
    assert user != "apikey"
    assert password != "apikey"


def test_key_is_not_passed_as_the_username(smtp, creds):
    """Order matters: login(user, password), not login(key, login)."""
    alerts.send_alert("s", "b")
    user, password = smtp.instances[0].login_args
    assert user == LOGIN and password == KEY
    assert (user, password) != (KEY, LOGIN)


def test_connection_uses_brevo_host_port_and_starttls(smtp, creds):
    alerts.send_alert("s", "b")
    server = smtp.instances[0]
    assert (server.host, server.port) == (alerts.SMTP_HOST, alerts.SMTP_PORT)
    assert server.host == "smtp-relay.brevo.com"
    assert server.port == 587
    assert server.starttls_called is True


def test_envelope_uses_alert_email_for_sender_and_recipient(smtp, creds):
    alerts.send_alert("s", "b")
    from_addr, to_addrs, _ = smtp.instances[0].sendmail_args
    assert EMAIL in from_addr
    assert to_addrs == [EMAIL]


# --- missing credentials ----------------------------------------------------------

@pytest.mark.parametrize("missing", ["BREVO_SMTP_LOGIN", "BREVO_SMTP_KEY", "ALERT_EMAIL"])
def test_missing_variable_is_named_and_no_connection_attempted(
        smtp, creds, monkeypatch, capsys, missing):
    monkeypatch.delenv(missing, raising=False)
    assert alerts.send_alert("subject", "body") is False
    assert smtp.instances == [], "must not attempt a doomed connection"
    out = capsys.readouterr().out
    assert missing in out, "the missing variable must be named"


def test_missing_login_explains_where_to_find_it(smtp, creds, monkeypatch, capsys):
    monkeypatch.delenv("BREVO_SMTP_LOGIN", raising=False)
    alerts.send_alert("s", "b")
    out = capsys.readouterr().out
    assert "SMTP & API" in out
    assert "smtp-brevo.com" in out


# --- rejected credentials ---------------------------------------------------------

def test_auth_rejection_gives_a_safe_actionable_diagnostic(monkeypatch, capsys, creds):
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)

    class Rejecting(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

    monkeypatch.setattr(alerts.smtplib, "SMTP", Rejecting)
    assert alerts.send_alert("s", "b") is False
    out = capsys.readouterr().out
    assert "535" in out
    assert "BREVO_SMTP_LOGIN" in out and "BREVO_SMTP_KEY" in out
    assert "not accepted" in out or "SMTP key" in out


def test_sender_refusal_points_at_verified_sender_requirement(monkeypatch, capsys, creds):
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)

    class RefusingSender(FakeSMTP):
        def sendmail(self, *a, **k):
            raise smtplib.SMTPSenderRefused(550, b"Sender not verified", EMAIL)

    monkeypatch.setattr(alerts.smtplib, "SMTP", RefusingSender)
    assert alerts.send_alert("s", "b") is False
    out = capsys.readouterr().out
    assert "verified sender" in out


# --- no credential ever reaches the log -------------------------------------------

def test_no_credential_value_is_ever_printed(monkeypatch, capsys, creds):
    """Success, auth rejection and generic failure must all keep secrets out of logs."""
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)

    class Rejecting(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

    class Exploding(FakeSMTP):
        def login(self, user, password):
            raise OSError("connection reset")

    for cls in (FakeSMTP, Rejecting, Exploding):
        monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
        monkeypatch.setattr(alerts.smtplib, "SMTP", cls)
        alerts.send_alert("subject", "body")
        out = capsys.readouterr().out
        assert KEY not in out, f"SMTP key leaked to stdout via {cls.__name__}"
        assert LOGIN not in out, f"SMTP login leaked to stdout via {cls.__name__}"


def test_generic_failure_names_the_exception_type_not_credentials(
        monkeypatch, capsys, creds):
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)

    class Exploding(FakeSMTP):
        def login(self, user, password):
            raise TimeoutError("timed out")

    monkeypatch.setattr(alerts.smtplib, "SMTP", Exploding)
    assert alerts.send_alert("s", "b") is False
    out = capsys.readouterr().out
    assert "TimeoutError" in out
    assert KEY not in out and LOGIN not in out
