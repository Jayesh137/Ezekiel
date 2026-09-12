# tests/test_alert_foreign_destination.py
"""The foreign-destination alert names the chain a Circle withdrawal went to.

`sendToEvmWithData` lands USDC at any recipient on any CCTP chain; the ledger
shows only a send to 0x2000…0000. The alert is the operator's only pointer to
WHERE to look, so the chain has to be in it, and it has to escalate."""

from src import alerts

T = "0x45d26f28196d226497130c4bac709d808fed4029"
DEST = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"


def _capture(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(
                            {"key": key, "hours": hours, "subject": subject, "body": body}) or True)
    return sent


def test_a_cctp_withdrawal_alert_is_critical_and_names_the_chain(monkeypatch):
    sent = _capture(monkeypatch)
    assert alerts.alert_foreign_destination(T, "sendToEvmWithData", DEST, "2500000", "USDC",
                                            "2026-09-12T00:00:00+00:00", "0x159e", chain="base")
    (a,) = sent
    assert alerts._severity_of(a["subject"]) == "CRITICAL"
    assert "on base" in a["subject"]
    assert "Chain: base" in a["body"]
    assert "sendToEvmWithData" in a["body"]
    assert DEST in a["body"] and "2500000" in a["body"]
    assert a["key"] == f"foreign_sendToEvmWithData_{DEST}"


def test_an_unknown_chain_is_still_alerted_not_dropped(monkeypatch):
    sent = _capture(monkeypatch)
    alerts.alert_foreign_destination(T, "sendToEvmWithData", DEST, "1", "USDC", None, None,
                                     chain="domain-99")
    assert "on domain-99" in sent[0]["subject"] and "Chain: domain-99" in sent[0]["body"]


def test_other_kinds_are_unchanged_and_carry_no_chain_line(monkeypatch):
    sent = _capture(monkeypatch)
    alerts.alert_foreign_destination(T, "withdraw3", DEST, "1", "USDC", None, None)
    assert alerts._severity_of(sent[0]["subject"]) == "CRITICAL"
    assert "Chain:" not in sent[0]["body"]
