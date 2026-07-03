# tests/test_dedup_and_alerts.py
"""Targeted tests for changed critical behavior: batch dedup and alert cooldowns."""
import tempfile

from src.utils import append_records, load_all_records
from src import alerts
from src import tracer


def test_append_records_dedupes_within_batch():
    with tempfile.TemporaryDirectory() as d:
        records = [
            {"tid": 1, "coin": "BTC"},
            {"tid": 1, "coin": "BTC"},  # within-batch dup
            {"tid": 2, "coin": "ETH"},
        ]
        assert append_records(d, records, key_field="tid") == 2
        assert len(load_all_records(d)) == 2


def test_append_records_keyless_records_not_deduped():
    with tempfile.TemporaryDirectory() as d:
        records = [{"coin": "BTC"}, {"coin": "ETH"}]  # no key field
        assert append_records(d, records, key_field="tid") == 2


def test_append_records_dedupes_against_existing():
    with tempfile.TemporaryDirectory() as d:
        append_records(d, [{"tid": 1}], key_field="tid")
        assert append_records(d, [{"tid": 1}, {"tid": 3}], key_field="tid") == 1


def test_alert_cooldown(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(alerts, "send_alert", lambda s, b, h=None: sent.append(s) or True)
    monkeypatch.setattr(alerts, "read_cursor", lambda name: cursors.get(name, 0))
    monkeypatch.setattr(alerts, "write_cursor", lambda name, v: cursors.__setitem__(name, v))
    cursors = {}

    assert alerts.alert_behavioral_match("0xABC", 0.95, {"timing_profile": 0.9}) is True
    assert alerts.alert_behavioral_match("0xABC", 0.95, {"timing_profile": 0.9}) is False
    assert alerts.alert_behavioral_match("0xDEF", 0.95, {"timing_profile": 0.9}) is True
    assert len(sent) == 2


def test_unique_destinations_dedupes_and_skips_dust():
    wallet = "0xWALLET"
    outbound = [
        {"to": "0xDEST", "value": "0"},          # zero-value dust -> dropped
        {"to": "0xDEST", "value": "0"},          # more dust to same dest
        {"to": "0xDEST", "value": "5000000"},    # real 5 USDC -> representative
        {"to": "0xDEST", "value": "1000000"},    # smaller, same dest -> collapsed
        {"to": wallet, "value": "9000000"},      # self-transfer -> dropped
        {"to": "0xOTHER", "value": "2000000"},   # distinct dest
    ]
    result = tracer.unique_destinations(outbound, wallet)
    dests = [t["to"] for t in result]
    assert dests == ["0xDEST", "0xOTHER"]                 # deduped, value-sorted
    assert result[0]["value"] == "5000000"                # kept largest per dest


def test_unique_destinations_respects_cap():
    outbound = [{"to": f"0x{i:040x}", "value": "1000000"} for i in range(200)]
    assert len(tracer.unique_destinations(outbound, "0xWALLET")) == tracer.MAX_DESTINATIONS


def test_send_alert_short_circuits_after_failure(monkeypatch):
    monkeypatch.setattr(alerts, "_smtp_disabled_this_run", False)
    monkeypatch.setenv("BREVO_SMTP_KEY", "key")
    monkeypatch.setenv("ALERT_EMAIL", "me@example.com")

    attempts = []

    def boom(*a, **k):
        attempts.append(1)
        raise OSError("(535, b'5.7.8 Authentication failed')")

    monkeypatch.setattr(alerts.smtplib, "SMTP", boom)

    assert alerts.send_alert("s1", "b1") is False   # attempts a real connect, fails
    assert alerts.send_alert("s2", "b2") is False   # short-circuits, no connect
    assert len(attempts) == 1
