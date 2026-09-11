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
    # Email is the only channel these tests configure. Cleared rather than
    # assumed absent: an operator's own NTFY_TOPIC or a GITHUB_TOKEN exported
    # in the shell would make these tests POST to ntfy.sh or open a real
    # issue — a network call in a suite that must have none, and a result
    # that depends on whose machine it runs on.
    for var in ("NTFY_TOPIC", "NTFY_INCLUDE_INFO", "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
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


def _graph_with_one_info_discovery():
    """The same transfer with no corroboration: DIRECT_RECIPIENT, so the alert
    subject carries INFO rather than CRITICAL."""
    e = normalise_l1_transfer({
        "from": T, "to": W, "value": "900000000", "timeStamp": "1784000000",
        "hash": "0xabc", "tokenSymbol": "USDC"})
    return build_graph([e], T)


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
    sent, undelivered, withheld = transfer_graph.fire_alerts(graph, alerts_list)
    assert sent == 0
    assert undelivered == [W]
    assert withheld == 0

    monkeypatch.setattr("src.alerts.alert_transfer_graph_discovery",
                        lambda *a, **k: True)
    sent, undelivered, withheld = transfer_graph.fire_alerts(graph, alerts_list)
    assert sent == 1
    assert undelivered == []
    assert withheld == 0, "a MIGRATION_CANDIDATE is CRITICAL — never withheld"


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


# --- discovery liveness ----------------------------------------------------------------------

def test_a_stalled_frontier_alerts_with_what_the_outage_is_costing(monkeypatch, cursors):
    """The frontier is the only vector that finds an address nobody has seen.
    It died for two days in Sept 2026 and nothing said so — the graph kept
    rebuilding from known edges and every report looked healthy."""
    sent = {}
    monkeypatch.setattr(alerts, "send_alert",
                        lambda subject, body, html=None: sent.update(
                            subject=subject, body=body) or True)

    fired = alerts.alert_discovery_stalled(
        hours=38.1,
        last_expansion_at="2026-09-09T18:56:29+00:00",
        status="failed",
        error="sweep ok: could not read base, optimism, bsc",
        queued=17,
        top_queued="0xf078969e55cabf9ae3f26afeb5ec627b4430f19e")

    assert fired is True
    # HIGH, not CRITICAL: CRITICAL in this system means something about HIM.
    # This is a capability of OURS being down, and blurring the two teaches the
    # operator to discount the severity that matters most.
    assert sent["subject"].startswith("[EZEKIEL] HIGH:")
    assert "38.1" in sent["subject"] or "38.1" in sent["body"]
    # What it is costing has to be in the body, or the alert is just a status.
    assert "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e" in sent["body"]
    assert "17" in sent["body"]
    assert "sweep ok: could not read base, optimism, bsc" in sent["body"]


def test_a_stalled_frontier_pages_once_a_day_not_every_run(monkeypatch, cursors):
    """A multi-day outage is one problem. Trace runs every ~3h; without a
    cooldown this would be the "stop the phone buzzing 24 times" bug again."""
    calls = []
    monkeypatch.setattr(alerts, "send_alert",
                        lambda subject, body, html=None: calls.append(subject) or True)

    first = alerts.alert_discovery_stalled(hours=38.1, last_expansion_at=None,
                                           status="failed", error=None,
                                           queued=0, top_queued=None)
    second = alerts.alert_discovery_stalled(hours=41.2, last_expansion_at=None,
                                            status="failed", error=None,
                                            queued=0, top_queued=None)

    assert first is True and second is False
    assert len(calls) == 1


# --- the mirror rule: state MUST advance past an alert nothing will deliver ------
#
# The rule above — a failed send consumes no cooldown and stays queued — is
# right for a FAILURE. Applied to an alert this system deliberately does not
# route, it becomes a livelock, and it ran as one in production on 2026-09-11.
#
# INFO is gated out of the instant channels on purpose and out of the GitHub
# fallback too, so `send_alert` returns False having done exactly what policy
# asked. `_send_with_cooldown` then wrote no cursor, `fire_alerts` recorded the
# wallet undelivered, and `select_alerts` re-selects an undelivered wallet
# unconditionally — ahead of every confidence gate. So the same notice came
# round every run: "Operational Counterparty (33% confidence)" seven times
# between 17:13 and 21:52, each one evicting a real row from the 20-entry
# delivery record, and the wallet pinned in `undelivered_alerts` forever
# waiting for a retry that policy can never satisfy.
#
# A deliberately withheld alert is DISPOSED OF, not pending: it is on the
# dashboard and in the delivery record, and it must consume its cooldown and
# leave the retry queue. A genuine failure keeps every bit of its retryability.


def test_a_suppressed_info_consumes_its_cooldown(cursors, monkeypatch):
    """Policy withheld it, so there is nothing to retry — and nothing should
    come round again next run."""
    _smtp(monkeypatch, "auth")

    handled = alerts._send_with_cooldown(
        "tg_info", 48, "[EZEKIEL] INFO: Operational Counterparty (33% confidence)", "b")

    assert handled is True, "a withheld alert is handled, not failed"
    assert cursors.get("alert_tg_info"), "a withheld alert must consume its cooldown"


def test_a_failed_critical_still_consumes_no_cooldown(cursors, monkeypatch):
    """The original rule, pinned against the new one: severity decides whether
    an alert was withheld, and a CRITICAL is never withheld."""
    _smtp(monkeypatch, "auth")

    handled = alerts._send_with_cooldown(
        "tg_critical", 48, "[EZEKIEL] CRITICAL: Migration Candidate (84% confidence)", "b")

    assert handled is False
    assert "alert_tg_critical" not in cursors, "a real failure stays retryable"


def test_a_suppressed_info_discovery_leaves_the_retry_queue(cursors, monkeypatch):
    """End to end, on the loop that actually ran: an INFO discovery already in
    `undelivered_alerts` is re-selected, withheld again, and must come out of
    the queue rather than round again forever."""
    _smtp(monkeypatch, "auth")
    graph = _graph_with_one_info_discovery()
    queued = dict(graph)
    queued["undelivered_alerts"] = [W]

    selected = select_alerts(graph, queued)
    assert len(selected) == 1, "an undelivered wallet is re-selected unconditionally"
    node = selected[0]["node"]
    assert node["classification"] == "DIRECT_RECIPIENT", "INFO severity, not CRITICAL"

    delivered, undelivered, withheld = transfer_graph.fire_alerts(graph, selected)

    assert undelivered == [], "a withheld alert must not stay queued for retry"
    assert withheld == 1
    assert delivered == 0, (
        "nothing left the process — counting a withheld alert as delivered is "
        "the 'never say sent for a send that did not happen' rule again")


def test_only_policy_decides_what_is_withheld(monkeypatch):
    """`fire_alerts` must not carry its own copy of the severity rule. It asks
    alerts.py, so turning INFO back on flows through in one place."""
    for var in ("NTFY_INCLUDE_INFO",):
        monkeypatch.delenv(var, raising=False)

    assert alerts.discovery_withheld("DIRECT_RECIPIENT") is True
    assert alerts.discovery_withheld("OPERATIONAL_COUNTERPARTY") is True
    assert alerts.discovery_withheld("MIGRATION_CANDIDATE") is False
    assert alerts.discovery_withheld("POSSIBLE_LINKED_WALLET") is False

    monkeypatch.setenv("NTFY_INCLUDE_INFO", "1")
    assert alerts.discovery_withheld("DIRECT_RECIPIENT") is False, (
        "opting INFO back in makes it deliverable, so it is no longer withheld")
