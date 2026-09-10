# tests/test_record_dedupe.py
"""Stored records are deduplicated across day files when read.

append_records dedupes only inside the file it writes, and it files a record
under the collection date rather than the record's own date. Measured
2026-09-10: the ledger was stored three times over (1,430 rows, 500 unique
(hash, time) pairs) and 13.5% of fills were duplicates, so every HL-native
counterparty total was triple the truth and every exit reached the correlator
three times. The fix is at read time, exact-key, lossless.
"""

import json
from pathlib import Path

from src import transfer_graph as tg
from src.utils import load_all_records, record_key


def _write(d: Path, name: str, rows: list) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(rows))


def test_fills_are_deduped_by_tid_across_files(tmp_path):
    d = tmp_path / "fills"
    _write(d, "2026-02-20.json", [{"tid": 1, "px": "1"}, {"tid": 2, "px": "2"}])
    _write(d, "2026-07-03.json", [{"tid": 2, "px": "2"}, {"tid": 3, "px": "3"}])
    got = load_all_records(str(d))
    assert [r["tid"] for r in got] == [1, 2, 3]


def test_ledger_is_deduped_by_hash_and_time_across_files(tmp_path):
    d = tmp_path / "ledger"
    row = {"time": 10, "hash": "0xa", "delta": {"type": "withdraw", "usdc": "5"}}
    _write(d, "2026-02-20.json", [row])
    _write(d, "2026-02-21.json", [row])
    _write(d, "2026-07-03.json", [row, {"time": 11, "hash": "0xa", "delta": {}}])
    got = load_all_records(str(d))
    # Same hash at a different time is a different entry and is kept.
    assert [(r["hash"], r["time"]) for r in got] == [("0xa", 10), ("0xa", 11)]


def test_funding_ignores_the_zero_hash_and_keys_on_time_and_coin(tmp_path):
    d = tmp_path / "funding"
    zero = "0x" + "0" * 64
    rows = [{"time": 1, "hash": zero, "delta": {"coin": "BTC"}},
            {"time": 1, "hash": zero, "delta": {"coin": "ETH"}}]
    _write(d, "a.json", rows)
    _write(d, "b.json", rows)
    got = load_all_records(str(d))
    assert [(r["time"], r["delta"]["coin"]) for r in got] == [(1, "BTC"), (1, "ETH")]


def test_orders_are_deduped_by_oid(tmp_path):
    d = tmp_path / "orders"
    _write(d, "a.json", [{"oid": 7}, {"oid": 8}])
    _write(d, "b.json", [{"oid": 8}])
    assert [r["oid"] for r in load_all_records(str(d))] == [7, 8]


def test_unkeyed_directories_and_records_are_never_dropped(tmp_path):
    d = tmp_path / "scans"
    _write(d, "a.json", [{"x": 1}, {"x": 1}])
    _write(d, "b.json", [{"x": 1}])
    assert len(load_all_records(str(d))) == 3
    f = tmp_path / "fills"
    _write(f, "a.json", [{"px": "1"}, {"px": "1"}])   # no tid: kept, both
    assert len(load_all_records(str(f))) == 2


def test_dedupe_can_be_switched_off(tmp_path):
    d = tmp_path / "fills"
    _write(d, "a.json", [{"tid": 1}])
    _write(d, "b.json", [{"tid": 1}])
    assert len(load_all_records(str(d), dedupe=False)) == 2


def test_record_key_shapes():
    assert record_key("fills", {"tid": 5}) == 5
    assert record_key("ledger", {"hash": "0xa", "time": 1}) == ("0xa", 1)
    assert record_key("funding", {"time": 1, "delta": {"coin": "BTC"}}) == (1, "BTC")
    assert record_key("orders", {"oid": 9}) == 9
    assert record_key("anything", {"tid": 5}) is None
    assert record_key("fills", "not a dict") is None


# --- a token quantity is never a dollar value ------------------------------------

def _entry(delta):
    return {"time": 1_700_000_000_000, "hash": "0xh", "delta": delta}


def test_worthless_token_transfer_is_zero_not_its_quantity():
    """1,030,689,918 MAX with usdcValue 0.0 was booked as $1.03B from 0x207700bd."""
    e = tg.normalise_hl_ledger_entry(_entry({
        "type": "spotTransfer", "token": "MAX", "amount": "1030689918.58",
        "usdcValue": "0.0", "user": "0xaaa", "destination": "0xbbb"}))
    assert e["amount_usd"] == 0.0


def test_priced_token_transfer_uses_the_venue_valuation():
    e = tg.normalise_hl_ledger_entry(_entry({
        "type": "send", "token": "HYPE", "amount": "100000",
        "usdcValue": "3622800.0", "user": "0xaaa", "destination": "0xbbb"}))
    assert e["amount_usd"] == 3_622_800.0


def test_zero_usdc_falls_through_to_usdcvalue():
    e = tg.normalise_hl_ledger_entry(_entry({
        "type": "spotTransfer", "token": "HYPE", "amount": "1",
        "usdc": "0", "usdcValue": "36.0", "user": "0xaaa", "destination": "0xbbb"}))
    assert e["amount_usd"] == 36.0


def test_usdc_quantity_may_stand_in_for_its_own_value():
    e = tg.normalise_hl_ledger_entry(_entry({
        "type": "send", "token": "USDC", "amount": "2500000.0",
        "user": "0xaaa", "destination": "0xbbb"}))
    assert e["amount_usd"] == 2_500_000.0


def test_unpriced_non_usdc_quantity_is_zero():
    e = tg.normalise_hl_ledger_entry(_entry({
        "type": "spotTransfer", "token": "JPEG", "amount": "62500",
        "user": "0xaaa", "destination": "0xbbb"}))
    assert e["amount_usd"] == 0.0
