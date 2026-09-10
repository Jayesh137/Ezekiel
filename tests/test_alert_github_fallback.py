# tests/test_alert_github_fallback.py
"""A failed email must still reach the operator.

This deployment has never delivered an alert: `last_success_at` was null across
1,947 consecutive failures, with Brevo answering "your SMTP account is not yet
activated". The MIGRATION_CANDIDATE on the target's own treasury was written to
disk and never sent. A detector that cannot reach its operator is not a detector.

The danger in fixing it is the backlog. These tests pin the guards that keep a
recovered channel from opening thousands of issues.
"""

import pytest

import src.alerts as alerts


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(alerts, "_issues_opened_this_run", 0)
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setattr(alerts, "_record_delivery", lambda *a, **k: None)


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def _api(monkeypatch, *, existing=None, post_status=201):
    calls = {"get": 0, "post": []}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["get"] += 1
        return _Resp(200, existing or [])

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["post"].append(json)
        return _Resp(post_status)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def test_a_critical_alert_opens_an_issue(monkeypatch):
    calls = _api(monkeypatch)
    ok = alerts._github_issue_fallback(
        "tg_0xabc", "[EZEKIEL] CRITICAL: Migration Candidate (88% confidence)", "body")
    assert ok is True
    assert len(calls["post"]) == 1
    assert "tg_0xabc" in calls["post"][0]["title"]


def test_a_high_alert_opens_an_issue(monkeypatch):
    _api(monkeypatch)
    assert alerts._github_issue_fallback(
        "k", "[EZEKIEL] HIGH: Possible Linked Wallet (51% confidence)", "b") is True


def test_info_alerts_never_open_issues(monkeypatch):
    """There are hundreds of INFO alerts. Escalating them would bury the two
    that matter under noise, which is the same failure as sending nothing."""
    calls = _api(monkeypatch)
    assert alerts._github_issue_fallback(
        "k", "[EZEKIEL] INFO: Operational Counterparty (4% confidence)", "b") is False
    assert calls["post"] == []


def test_an_already_open_issue_is_not_duplicated(monkeypatch):
    """The trace job runs every 30 minutes. Without this it would open the same
    issue 48 times a day."""
    title = "[EZEKIEL] CRITICAL: Migration Candidate (88% confidence) [tg_0xabc]"
    calls = _api(monkeypatch, existing=[{"title": title}])
    ok = alerts._github_issue_fallback(
        "tg_0xabc", "[EZEKIEL] CRITICAL: Migration Candidate (88% confidence)", "b")
    assert ok is True          # it already reaches the operator
    assert calls["post"] == []  # but nothing new was created


def test_the_per_run_cap_stops_a_backlog_flood(monkeypatch):
    """1,947 alerts are undelivered. A channel that recovers must not translate
    that backlog into 1,947 issues."""
    calls = _api(monkeypatch)
    opened = [alerts._github_issue_fallback(
        f"key{i}", "[EZEKIEL] CRITICAL: Migration Candidate", "b") for i in range(6)]
    assert sum(opened) == alerts.MAX_ISSUES_PER_RUN
    assert len(calls["post"]) == alerts.MAX_ISSUES_PER_RUN


def test_no_token_is_a_silent_no_op(monkeypatch):
    """Every local run lacks a token. This must not make a developer's run
    behave differently from CI's, nor raise."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert alerts._github_issue_fallback(
        "k", "[EZEKIEL] CRITICAL: Migration Candidate", "b") is False


def test_an_api_failure_reports_false_rather_than_raising(monkeypatch):
    _api(monkeypatch, post_status=403)
    assert alerts._github_issue_fallback(
        "k", "[EZEKIEL] CRITICAL: Migration Candidate", "b") is False


def test_a_transport_exception_is_contained(monkeypatch):
    import requests

    def boom(*a, **k):
        raise ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    assert alerts._github_issue_fallback(
        "k", "[EZEKIEL] CRITICAL: Migration Candidate", "b") is False


def test_severity_parsing():
    assert alerts._severity_of("[EZEKIEL] CRITICAL: x") == "CRITICAL"
    assert alerts._severity_of("[EZEKIEL] HIGH: x") == "HIGH"
    assert alerts._severity_of("[EZEKIEL] INFO: x") == "INFO"
    assert alerts._severity_of("no severity here") == ""


def test_cooldown_starts_when_the_fallback_delivers(monkeypatch):
    """Otherwise the next run re-sends the same alert, and the dedupe check is
    the only thing standing between the operator and 48 issues a day."""
    written = []
    monkeypatch.setattr(alerts, "_cooldown_ok", lambda key, hours: True)
    monkeypatch.setattr(alerts, "send_alert", lambda s, b: False)
    monkeypatch.setattr(alerts, "_github_issue_fallback", lambda k, s, b: True)
    monkeypatch.setattr(alerts, "write_cursor", lambda k, v: written.append(k))

    assert alerts._send_with_cooldown("k", 48, "[EZEKIEL] CRITICAL: x", "b") is True
    assert written == ["alert_k"]


def test_no_cooldown_is_written_when_both_channels_fail(monkeypatch):
    """A failed alert must stay eligible to retry next run."""
    written = []
    monkeypatch.setattr(alerts, "_cooldown_ok", lambda key, hours: True)
    monkeypatch.setattr(alerts, "send_alert", lambda s, b: False)
    monkeypatch.setattr(alerts, "_github_issue_fallback", lambda k, s, b: False)
    monkeypatch.setattr(alerts, "write_cursor", lambda k, v: written.append(k))

    assert alerts._send_with_cooldown("k", 48, "[EZEKIEL] CRITICAL: x", "b") is False
    assert written == []


def test_scorer_alert_reports_the_numbers_and_warns_against_tuning(monkeypatch):
    """The failure's consequence is an ABSENCE of alerts, so the message has to
    carry the numbers and say plainly what is and is not still trustworthy —
    including that tuning weights until it passes would fit the very measurement
    that validates the scorer."""
    captured = {}
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda k, h, s, b: captured.update(key=k, subject=s, body=b) or True)

    alerts.alert_scorer_unreliable(0.5522, 0.5571, -0.0049, 2, 21,
                                   ["self-match ranked 2, not 1"])

    assert captured["key"] == "scorer_unreliable"
    assert "CRITICAL" in captured["subject"]
    body = captured["body"]
    assert "0.5522" in body and "0.5571" in body and "-0.0049" in body
    assert "2 of 21" in body
    assert "self-match ranked 2, not 1" in body
    # Names the vectors that still work, so the operator does not discard the
    # whole system over one broken one.
    assert "address" in body.lower() and "correlation" in body.lower()
    assert "Do NOT fix this by lowering thresholds" in body


def test_scorer_alert_survives_no_recorded_failures(monkeypatch):
    captured = {}
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda k, h, s, b: captured.update(body=b) or True)
    alerts.alert_scorer_unreliable(0.5, 0.6, -0.1, 3, 10, [])
    assert "(none recorded)" in captured["body"]


def test_a_fund_movement_alert_states_when_the_money_moved(monkeypatch):
    """An alert omitting the date reads as breaking news regardless of age. One
    fired for a transfer from 2025-10-17 and was taken for current activity — a
    cursor advancing, a queued alert finally delivering, or a newly swept wallet
    all surface old transfers."""
    captured = {}
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda k, h, s, b: captured.update(body=b) or True)
    alerts.alert_fund_movement("0xw", "$3,234,700.00", "0xd", "0xtx",
                               asset="USDC", chain="ethereum",
                               occurred_at="2025-10-17 19:47 UTC")
    assert "When: 2025-10-17 19:47 UTC" in captured["body"]


def test_an_undated_fund_movement_says_so_rather_than_implying_recency(monkeypatch):
    captured = {}
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda k, h, s, b: captured.update(body=b) or True)
    alerts.alert_fund_movement("0xw", "$1.00", "0xd", "0xtx")
    assert "When: unknown" in captured["body"]
    assert "historical transfer" in captured["body"]
