# tests/test_trace_store.py
import json

import pytest

from src.trace import store


def test_a_round_trip_keeps_every_record(tmp_path):
    table = {"0x4a" + "0" * 38: {"class": "hl_deposit"}, "0xf0" + "1" * 38: {"class": "cluster"}}
    store.save(tmp_path, table)
    assert store.load(tmp_path) == table
    assert sorted(p.name for p in tmp_path.glob("*.json")) == ["4a.json", "f0.json"]


def test_an_unchanged_shard_is_not_rewritten(tmp_path):
    table = {"0x4a" + "0" * 38: {"class": "hl_deposit"}, "0xf0" + "1" * 38: {"class": "cluster"}}
    store.save(tmp_path, table)
    table["0xf0" + "1" * 38]["class"] = "quiet_eoa"
    assert store.save(tmp_path, table) == ["f0"]


def test_a_corrupt_shard_is_an_error_not_empty_state(tmp_path):
    (tmp_path / "4a.json").write_text("{not json")
    with pytest.raises(RuntimeError, match="unreadable trace shard"):
        store.load(tmp_path)


def test_a_missing_directory_is_a_first_run(tmp_path):
    assert store.load(tmp_path / "nothing") == {}


def test_shards_are_compact_json(tmp_path):
    store.save(tmp_path, {"0x4a" + "0" * 38: {"a": 1}})
    assert json.loads((tmp_path / "4a.json").read_text()) == {"0x4a" + "0" * 38: {"a": 1}}
    assert "\n" not in (tmp_path / "4a.json").read_text()
