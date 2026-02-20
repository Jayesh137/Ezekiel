# tests/test_utils.py
import json
import os
import tempfile
import pytest
from src.utils import (
    read_cursor, write_cursor, append_records,
    load_all_records, deduplicate_by_key, today_str, now_hhmm
)

def test_read_cursor_missing():
    assert read_cursor("nonexistent", base="/tmp/test_state") == 0

def test_write_and_read_cursor():
    with tempfile.TemporaryDirectory() as d:
        write_cursor("test_cursor", 1700000000000, base=d)
        assert read_cursor("test_cursor", base=d) == 1700000000000

def test_append_records_dedup():
    with tempfile.TemporaryDirectory() as d:
        records = [
            {"hash": "0xaaa", "coin": "BTC"},
            {"hash": "0xbbb", "coin": "ETH"},
        ]
        added = append_records(d, records, key_field="hash")
        assert added == 2

        # Append again with one duplicate and one new
        records2 = [
            {"hash": "0xaaa", "coin": "BTC"},  # duplicate
            {"hash": "0xccc", "coin": "SOL"},   # new
        ]
        added2 = append_records(d, records2, key_field="hash")
        assert added2 == 1

        # Verify total
        all_records = load_all_records(d)
        assert len(all_records) == 3

def test_deduplicate_by_key():
    records = [
        {"id": 1, "val": "a"},
        {"id": 2, "val": "b"},
        {"id": 1, "val": "a"},  # dup
    ]
    deduped = deduplicate_by_key(records, "id")
    assert len(deduped) == 2

def test_today_str_format():
    s = today_str()
    assert len(s) == 10  # YYYY-MM-DD
    assert s[4] == "-" and s[7] == "-"

def test_now_hhmm_format():
    s = now_hhmm()
    assert len(s) == 5  # HH-MM
    assert s[2] == "-"
