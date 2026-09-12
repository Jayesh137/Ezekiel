# tests/test_alert_severity_vocabulary.py
"""Every alert must carry a severity the router recognises.

The severity token in an alert subject is not decoration — it IS the routing
key. `_send_webhooks` and `_github_issue_fallback` both gate on
ESCALATING_SEVERITIES, and `_health_bearing` treats an unclassifiable subject as
health-bearing precisely so an alert nobody can read is never assumed harmless.

Put those together and a severity outside the vocabulary is the worst of both:
the alert reaches no channel at all, falls through to email (which has never
delivered), and then marks the whole alerting system unhealthy for failing.

Measured live 2026-09-12: three live alert paths invented their own words.
`alert_risk_level` interpolated risk.py's level straight into the subject, so
the unified migration-risk alert went out as "ELEVATED" — it fired twice that
morning, both times to nowhere, and left `healthy: false` with the dashboard
announcing ALERTING IS DOWN while every CRITICAL that day arrived on ntfy in
seconds. `alert_account_value_drop` ("Possible Liquidation") and
`alert_target_silence` used "WARNING" the same way and would have done the same
the moment they fired.

That is the INFO-flood lesson inverted: there, an alert buzzed when it should
not have; here, three alerts about HIM could not buzz at all. A flag pinned
false by our own vocabulary cannot report a real outage either.
"""

import re
from pathlib import Path

from src import alerts

ALERTS_PY = Path(__file__).resolve().parent.parent / "src" / "alerts.py"

# The only tokens `_severity_of` can read. Anything else is unroutable.
KNOWN = ("CRITICAL", "HIGH", "INFO")

SUBJECT = re.compile(r"\[EZEKIEL\]\s*(?P<sev>[^:\"']*?):")


def test_every_LITERAL_severity_in_alerts_py_is_routable():
    """A hard-coded severity must be one the router reads.

    Interpolated ones (`{severity}`, `{discovery_severity(...)}`) are checked at
    runtime instead, over the whole domain their source can produce: a static
    reader cannot tell a safe variable from an unsafe one, and failing them here
    would only teach the next author to hide a literal behind a variable.
    """
    offenders = []
    for n, line in enumerate(ALERTS_PY.read_text(encoding="utf-8").splitlines(), 1):
        for m in SUBJECT.finditer(line):
            sev = m.group("sev").strip()
            if "{" in sev:
                continue
            if sev not in KNOWN:
                offenders.append(f"alerts.py:{n}: severity {sev!r} is not routable")
    assert not offenders, "\n".join(offenders)


def test_every_interpolated_severity_source_only_yields_known_tokens():
    """The other half: each function feeding a `{severity}` slot, over its domain."""
    for classification in ("MIGRATION_CANDIDATE", "POSSIBLE_LINKED_WALLET",
                           "OPERATIONAL_COUNTERPARTY", "DIRECT_RECIPIENT", "unheard of"):
        assert alerts.discovery_severity(classification) in KNOWN


def test_severity_of_reads_every_known_token():
    for sev in KNOWN:
        assert alerts._severity_of(f"[EZEKIEL] {sev}: something") == sev


def test_an_unknown_severity_is_unroutable_and_health_bearing():
    """The property that makes an invented word cost twice."""
    subject = "[EZEKIEL] ELEVATED: Migration Risk 54/100"
    assert alerts._severity_of(subject) == ""
    assert alerts._severity_of(subject) not in alerts.ESCALATING_SEVERITIES
    assert alerts._health_bearing(subject) is True


def test_risk_level_alerts_map_onto_the_routing_vocabulary(monkeypatch):
    """risk.py speaks LOW/GUARDED/ELEVATED/CRITICAL; the router speaks its own."""
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(subject) or True)
    for level, expected in (("CRITICAL", "CRITICAL"), ("ELEVATED", "HIGH")):
        sent.clear()
        alerts.alert_risk_level(72.0, level, [{"points": 12, "label": "a factor"}], "0xaa")
        assert alerts._severity_of(sent[0]) == expected
        assert alerts._severity_of(sent[0]) in alerts.ESCALATING_SEVERITIES
        assert level.title() in sent[0] or "Migration Risk" in sent[0]


def test_the_risk_level_is_still_named_in_the_alert(monkeypatch):
    """Mapping the severity must not hide WHICH level was reached."""
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((subject, body)) or True)
    alerts.alert_risk_level(54.0, "ELEVATED", [{"points": 9, "label": "f"}], "0xaa")
    subject, body = sent[0]
    assert "ELEVATED" in subject or "ELEVATED" in body


def _captured_subject(monkeypatch, call):
    """These two send directly rather than through the cooldown wrapper."""
    sent = []
    monkeypatch.setattr(alerts, "send_alert",
                        lambda subject, body, **k: sent.append(subject) or True)
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(subject) or True)
    call()
    assert sent, "the alert sent nothing at all"
    return sent[0]


def test_the_liquidation_alert_reaches_a_channel(monkeypatch):
    subject = _captured_subject(
        monkeypatch,
        lambda: alerts.alert_account_value_drop(10_000_000.0, 20_000_000.0, 0.5, None))
    assert alerts._severity_of(subject) in alerts.ESCALATING_SEVERITIES


def test_the_silence_alert_reaches_a_channel(monkeypatch):
    subject = _captured_subject(monkeypatch, lambda: alerts.alert_target_silence(4.2))
    assert alerts._severity_of(subject) in alerts.ESCALATING_SEVERITIES
