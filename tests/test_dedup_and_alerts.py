# tests/test_dedup_and_alerts.py
"""Targeted tests for changed critical behavior: batch dedup and alert cooldowns."""
import tempfile

from src.utils import append_records, load_all_records
from src import alerts


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
