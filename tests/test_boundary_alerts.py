"""Boundary alerts speak the routing vocabulary and never page a recorded row."""

from src import alerts
from src.boundary import attribution as at

KNOWN = ("CRITICAL", "HIGH")


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, subject, body)) or True)
    return sent


def _row(**kw):
    base = {"kind": at.KIND_PAID_HIS_WORLD, "severity": "CRITICAL", "vote": "linkage",
            "role": "deposit", "member": "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e",
            "member_why": "his private exchange deposit address", "source": "bridge2",
            "hl_account": "0x" + "1" * 40, "counterparty": "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e",
            "amount_usd": 250_000.0, "ts": 1_790_000_000, "ref": "0xabc", "chain": "arbitrum",
            "key": "k1", "retro": False}
    base.update(kw)
    return base


def test_a_boundary_finding_routes_with_its_severity(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_boundary_finding(_row())
    key, subject, body = sent[0]
    assert alerts._severity_of(subject) == "CRITICAL"
    assert "0x" + "1" * 40 in body and "deposit" in body and "$250,000" in body


def test_history_is_labelled_and_recorded_rows_never_send(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_boundary_finding(_row(severity="HIGH", retro=True))
    assert "historical" in sent[0][1].lower() and alerts._severity_of(sent[0][1]) == "HIGH"
    assert not alerts.alert_boundary_finding(_row(severity=None))
    assert len(sent) == 1


def test_every_title_and_severity_is_routable(monkeypatch):
    sent = _capture(monkeypatch)
    for kind in at.TITLES:
        for sev in KNOWN:
            alerts.alert_boundary_finding(_row(kind=kind, severity=sev, key=f"{kind}{sev}"))
    assert len(sent) == len(at.TITLES) * 2
    assert all(alerts._severity_of(s) in KNOWN for _, s, _ in sent)


def test_provenance_and_perimeter_alerts(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_provenance_hit({
        "account": "0x" + "4" * 40, "severity": "CRITICAL", "hop": 1, "role": "core",
        "member": "0x45d26f28196d226497130c4bac709d808fed4029", "route": "bridge2",
        "usd": 3e6, "ts": 1_790_000_000, "key": "p1", "via": None})
    assert alerts.alert_perimeter_hl_account(
        {"address": "0x" + "3" * 40, "role": "sink", "why": "quiet wallet holding $500,000"},
        {"account_value": 25_000.0, "all_time_volume": 2e6, "birth": "2026-09-01"})
    assert [alerts._severity_of(s) for _, s, _ in sent] == ["CRITICAL", "HIGH"]
