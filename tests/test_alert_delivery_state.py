# tests/test_alert_delivery_state.py
"""Delivery state must never advance past an undelivered alert.

A MIGRATION_CANDIDATE was selected, its send failed with 535, and the graph was
saved anyway. The next run compared against that saved state, saw no change, and
never retried — so the discovery was lost silently, with no log line at all and
zero Brevo transactional logs.

Two independent state machines had the same defect:
  transfer_graph.run_transfer_graph  saved the graph regardless of delivery
  ledger_analyzer.check_new_outbound_transfers  advanced its cursor regardless

These tests pin: a failed send consumes no cooldown, advances no cursor, and
leaves the discovery queued for retry.
"""

import smtplib

import pytest

from src import alerts, ledger_analyzer, transfer_graph
from src.transfer_graph import build_graph, normalise_l1_transfer, select_alerts

T = "0x45d26f28196d226497130c4bac709d808fed4029"
W = "0x1111111111111111111111111111111111111111"
LOGIN = "sentinel-login@smtp-brevo.example"
KEY = "sentinel-smtp-key"
EMAIL = "recipient@example.com"


@pytest.fixture
def cursors(monkeypatch):
    """In-memory cursor store so no production state is read or written."""
    store = {}
    monkeypatch.setattr(alerts, "read_cursor", lambda n: store.get(n, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setenv("BREVO_SMTP_LOGIN", LOGIN)
    monkeypatch.setenv("BREVO_SMTP_KEY", KEY)
    monkeypatch.setenv("ALERT_EMAIL", EMAIL)
    return store


def _smtp(monkeypatch, behaviour):
    """Install a fake SMTP whose login/sendmail behaviour is parameterised."""
    class Fake:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            if behaviour == "auth":
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

        def sendmail(self, *a, **k):
            if behaviour == "sender":
                raise smtplib.SMTPSenderRefused(550, b"Sender not verified", EMAIL)

    monkeypatch.setattr(alerts.smtplib, "SMTP", Fake)


# --- cooldown is consumed only by a real delivery ---------------------------------

def test_successful_send_records_success_and_consumes_cooldown(cursors, monkeypatch):
    _smtp(monkeypatch, "ok")
    assert alerts._send_with_cooldown("k1", 24, "subject", "body") is True
    assert cursors.get("alert_k1"), "successful send must record the cooldown"


def test_authentication_failure_does_not_consume_cooldown(cursors, monkeypatch):
    _smtp(monkeypatch, "auth")
    assert alerts._send_with_cooldown("k2", 24, "subject", "body") is False
    assert "alert_k2" not in cursors, "failed auth must not consume the cooldown"


def test_sender_failure_does_not_consume_cooldown(cursors, monkeypatch):
    _smtp(monkeypatch, "sender")
    assert alerts._send_with_cooldown("k3", 24, "subject", "body") is False
    assert "alert_k3" not in cursors, "refused sender must not consume the cooldown"


def test_failed_then_fixed_send_still_delivers(cursors, monkeypatch):
    """The whole point: a failure must leave the alert retryable."""
    _smtp(monkeypatch, "auth")
    assert alerts._send_with_cooldown("k4", 24, "s", "b") is False
    assert "alert_k4" not in cursors

    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    _smtp(monkeypatch, "ok")
    assert alerts._send_with_cooldown("k4", 24, "s", "b") is True
    assert cursors.get("alert_k4")


def test_skipped_alert_logs_the_exact_reason(cursors, monkeypatch, capsys):
    _smtp(monkeypatch, "ok")
    assert alerts._send_with_cooldown("k5", 24, "first", "b") is True
    capsys.readouterr()

    assert alerts._send_with_cooldown("k5", 24, "second", "b") is False
    out = capsys.readouterr().out
    assert "Cooldown active" in out
    assert "k5" in out, "the cooldown key must be named"
    assert "second" in out, "the suppressed subject must be named"


def test_smtp_disabled_short_circuit_states_why(cursors, monkeypatch, capsys):
    _smtp(monkeypatch, "auth")
    alerts.send_alert("first", "b")
    capsys.readouterr()
    alerts.send_alert("second", "b")
    out = capsys.readouterr().out
    assert "SMTP disabled after earlier failure" in out
    assert "second" in out


# --- transfer graph: undelivered discoveries are retried --------------------------

def _graph_with_one_discovery():
    e = normalise_l1_transfer({
        "from": T, "to": W, "value": "900000000", "timeStamp": "1784000000",
        "hash": "0xabc", "tokenSymbol": "USDC"})
    return build_graph([e], T, behavioural={W: 0.86}, hl_active={W},
                       correlations={W: {"confidence": 0.9, "gap_hours": 2.0}})


def test_undelivered_alert_is_reselected_next_run(monkeypatch):
    graph = _graph_with_one_discovery()
    first = select_alerts(graph, None)
    assert len(first) == 1

    # Delivery failed: the saved graph records it as undelivered.
    saved = dict(graph)
    saved["undelivered_alerts"] = [W]
    assert len(select_alerts(graph, saved)) == 1, "undelivered alert must be retried"
    assert select_alerts(graph, saved)[0]["trigger_reasons"] == [
        "retry: previously selected but not delivered"]

    # Delivery succeeded: nothing is re-selected.
    delivered = dict(graph)
    delivered["undelivered_alerts"] = []
    assert select_alerts(graph, delivered) == []


def test_fire_alerts_reports_undelivered_wallets(monkeypatch):
    graph = _graph_with_one_discovery()
    alerts_list = select_alerts(graph, None)

    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: False)
    sent, undelivered = transfer_graph.fire_alerts(graph, alerts_list)
    assert sent == 0
    assert undelivered == [W]

    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: True)
    sent, undelivered = transfer_graph.fire_alerts(graph, alerts_list)
    assert sent == 1
    assert undelivered == []


def test_state_advance_without_the_fix_would_lose_the_alert():
    """Documents the original defect: saving the graph as-is retires the alert."""
    graph = _graph_with_one_discovery()
    assert len(select_alerts(graph, None)) == 1
    # Pre-fix behaviour — saved graph carries no undelivered marker.
    assert select_alerts(graph, graph) == [], "this is what silently dropped it"


# --- ledger analyzer: cursor held until delivery -----------------------------------

def _counterparty(ms):
    return {"wallet": W, "total_out_usd": 1_000_000, "total_in_usd": 0,
            "bidirectional": False, "tokens": ["USDC"], "known_self": False,
            "last_seen_ms": ms}


def test_failed_hl_transfer_alert_does_not_advance_cursor(monkeypatch, capsys):
    store = {}
    monkeypatch.setattr(ledger_analyzer, "read_cursor", lambda n: store.get(n, 0))
    monkeypatch.setattr(ledger_analyzer, "write_cursor", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(ledger_analyzer, "load_config",
                        lambda: {"hl_transfer": {"min_usdc_alert": 50000}})
    monkeypatch.setattr("src.alerts.alert_hl_native_transfer", lambda *a, **k: False)

    result = {"counterparties": [_counterparty(1_700_000_000_000)]}
    assert ledger_analyzer.check_new_outbound_transfers(result) == []
    assert "last_hl_transfer_alert_ms" not in store, \
        "a failed send must not advance the watermark"
    assert "NOT delivered" in capsys.readouterr().out

    # Once delivery works, the same transfer alerts and the cursor advances.
    monkeypatch.setattr("src.alerts.alert_hl_native_transfer", lambda *a, **k: True)
    assert len(ledger_analyzer.check_new_outbound_transfers(result)) == 1
    assert store["last_hl_transfer_alert_ms"] == 1_700_000_000_000


def test_successful_hl_transfer_alert_advances_cursor_once(monkeypatch):
    store = {}
    monkeypatch.setattr(ledger_analyzer, "read_cursor", lambda n: store.get(n, 0))
    monkeypatch.setattr(ledger_analyzer, "write_cursor", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(ledger_analyzer, "load_config",
                        lambda: {"hl_transfer": {"min_usdc_alert": 50000}})
    monkeypatch.setattr("src.alerts.alert_hl_native_transfer", lambda *a, **k: True)

    result = {"counterparties": [_counterparty(1_700_000_000_000)]}
    assert len(ledger_analyzer.check_new_outbound_transfers(result)) == 1
    assert store["last_hl_transfer_alert_ms"] == 1_700_000_000_000
    # Second run: at/below the cursor, so it is skipped rather than re-alerted.
    assert ledger_analyzer.check_new_outbound_transfers(result) == []
