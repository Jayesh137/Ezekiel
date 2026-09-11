# tests/test_utils.py
import json
import tempfile
from pathlib import Path

from src.utils import (
    append_records,
    atomic_write_json,
    deduplicate_by_key,
    load_all_records,
    now_hhmm,
    read_cursor,
    save_latest,
    today_str,
    write_cursor,
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


# --- atomic_write_json: shared by save_latest and src/tracer.py's marker -------

def test_atomic_write_json_writes_valid_json_and_makes_parent_dirs():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "nested" / "marker.json"
        atomic_write_json(path, {"a": 1})
        assert json.loads(path.read_text()) == {"a": 1}


def test_atomic_write_json_honours_sort_keys():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "marker.json"
        atomic_write_json(path, {"b": 1, "a": 2}, sort_keys=True)
        text = path.read_text()
        assert text.index('"a"') < text.index('"b"')


def test_atomic_write_json_leaves_no_temp_file_behind():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "marker.json"
        atomic_write_json(path, {"a": 1})
        leftovers = [p.name for p in Path(d).iterdir() if p.name != "marker.json"]
        assert leftovers == []


def test_save_latest_still_produces_the_same_shape_after_the_refactor():
    """save_latest is now a thin wrapper over atomic_write_json; this pins that
    the refactor did not change its filename or output."""
    with tempfile.TemporaryDirectory() as d:
        filepath = save_latest(d, {"x": 1})
        assert filepath == str(Path(d) / "latest.json")
        assert json.loads(Path(filepath).read_text()) == {"x": 1}


def test_cursor_names_are_filesystem_safe(tmp_path):
    """CI wrote data/state/alert_foreign_bridge:cctp->solana_0x1d24….txt and
    committed it; `git checkout main` then failed outright on Windows, because
    ':' and '>' are not legal in an NTFS filename. A repository the operator
    cannot check out is worse than any alert."""
    from src.utils import cursor_filename, read_cursor, write_cursor

    nasty = "foreign_bridge:cctp->solana_0xAB*?|<>"
    # Hyphen, dot and underscore survive; everything else becomes an underscore.
    assert cursor_filename(nasty) == "foreign_bridge_cctp-_solana_0xAB_____"
    assert cursor_filename("plain_key-1.2") == "plain_key-1.2"
    assert cursor_filename("") == "cursor"

    # Its own directory: the sandbox fixture already made tmp_path/"data".
    state = tmp_path / "state"
    write_cursor(nasty, 1234, base=str(state))
    written = [p.name for p in state.iterdir()]
    assert len(written) == 1
    assert not any(c in written[0] for c in ':*?|<>')
    assert read_cursor(nasty, base=str(state)) == 1234
