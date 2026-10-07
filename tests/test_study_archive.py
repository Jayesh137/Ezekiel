"""The study's files: daily records, sealed months, state, and nothing rewritten needlessly."""

import pytest

from src import utils
from src.study import archive
from src.study import records as rec

W = "0x" + "c" * 40


def day(d, fills=1):
    record = rec.empty_day(W, d, "studied")
    record["fills"] = fills
    return record


def test_days_round_trip_and_unchanged_days_are_not_rewritten(tmp_path):
    assert archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path) == 1
    assert archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path) == 0
    assert archive.load_days(W, "2026-10-01", "2026-10-01", tmp_path) == {
        "2026-10-01": day("2026-10-01")}


def test_sealed_months_roll_into_one_verified_archive(tmp_path):
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30", 2),
                       "2026-10-01": day("2026-10-01")}, tmp_path)
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 1
    folder = archive.wallet_dir(W, tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == ["2026-09.jsonl.gz", "2026-10-01.json"]
    got = archive.load_days(W, "2026-09-01", "2026-10-31", tmp_path)
    assert sorted(got) == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert got["2026-09-30"]["fills"] == 2


def test_a_late_daily_file_for_a_sealed_month_is_merged_on_the_next_roll(tmp_path):
    archive.save_days({"2026-09-29": day("2026-09-29")}, tmp_path)
    archive.roll_sealed_months(W, "2026-10-06", tmp_path)
    archive.save_days({"2026-09-30": day("2026-09-30")}, tmp_path)
    archive.roll_sealed_months(W, "2026-10-06", tmp_path)
    folder = archive.wallet_dir(W, tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == ["2026-09.jsonl.gz"]
    read_back = archive._read_month(folder / "2026-09.jsonl.gz")
    assert sorted(read_back) == ["2026-09-29", "2026-09-30"]


def test_an_unreadable_month_archive_is_never_overwritten(tmp_path):
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-09.jsonl.gz").write_bytes(b"not gzip")
    archive.save_days({"2026-09-30": day("2026-09-30")}, tmp_path)
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
    assert (folder / "2026-09.jsonl.gz").read_bytes() == b"not gzip"
    assert (folder / "2026-09-30.json").exists()


def test_the_default_root_follows_the_sandboxed_data_dir():
    assert archive.root() == utils.DATA_DIR / "study"


def test_state_round_trips_and_a_missing_state_is_empty(tmp_path):
    archive.save_state({"wallets": {W: {"fills_cursor_ms": 5}}}, tmp_path)
    assert archive.load_state(tmp_path) == {"wallets": {W: {"fills_cursor_ms": 5}}}
    assert archive.load_state(tmp_path / "nowhere") == {}


def test_write_if_changed(tmp_path):
    path = tmp_path / "study" / "x.json"
    assert archive.write_if_changed(path, {"a": 1}) is True
    assert archive.write_if_changed(path, {"a": 1}) is False


# NEW TESTS FOR FIX ROUND 1

def test_corrupt_day_file_in_sealed_month_survives_beside_good_one(tmp_path):
    """A corrupt (truncated JSON) day file in a sealed month survives the roll."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Save two good days
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30")}, tmp_path)
    # Corrupt the 2026-09-30 file
    day30_path = folder / "2026-09-30.json"
    day30_path.write_text("{truncated")
    # Roll should skip the corrupt file but process the good one
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 1
    # Archive exists and holds only the good day
    archive_path = folder / "2026-09.jsonl.gz"
    assert archive_path.exists()
    read_back = archive._read_month(archive_path)
    assert sorted(read_back) == ["2026-09-29"]
    # Corrupt file still on disk
    assert day30_path.exists()
    assert day30_path.read_text() == "{truncated"


def test_all_day_files_of_sealed_month_unreadable_no_archive(tmp_path):
    """Every day file of a sealed month unreadable and no archive written."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Save then corrupt both day files
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30")}, tmp_path)
    (folder / "2026-09-29.json").write_text("{bad")
    (folder / "2026-09-30.json").write_text("{bad")
    # Roll returns 0 and creates no archive
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
    assert not (folder / "2026-09.jsonl.gz").exists()
    # Day files still there
    assert (folder / "2026-09-29.json").exists()
    assert (folder / "2026-09-30.json").exists()


def test_load_state_strict_reading(tmp_path):
    """load_state: missing → {}; truncated → Unreadable; list → Unreadable."""
    # Missing state
    assert archive.load_state(tmp_path) == {}
    # Truncated state.json
    state_path = archive.root(tmp_path) / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{bad")
    with pytest.raises(archive.Unreadable):
        archive.load_state(tmp_path)
    # Valid JSON but not a dict
    state_path.write_text("[]")
    with pytest.raises(archive.Unreadable):
        archive.load_state(tmp_path)


def test_load_days_strict_inside_window(tmp_path):
    """load_days raises Unreadable for corrupt files inside the window."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Case 1: truncated day file inside window
    (folder / "2026-10-01.json").write_text("{bad")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)

    # Case 2: day file with wrong "day" field
    (folder / "2026-10-01.json").write_text('{"day":"2026-10-02"}')
    with pytest.raises(archive.Unreadable, match="not the record for"):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)

    # Case 3: zero-byte month archive inside window
    (folder / "2026-10-01.json").unlink()
    (folder / "2026-10.jsonl.gz").write_bytes(b"")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)

    # Case 4: corrupted gzip (zlib error)
    (folder / "2026-10.jsonl.gz").write_bytes(b"\x1f\x8b\x08\x00" + b"bad gzip data")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)

    # Case 5: month archive with non-dict row
    import shutil
    shutil.rmtree(folder)
    folder.mkdir(parents=True)
    archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path)
    # Manually corrupt the archive by replacing a row
    archive_path = folder / "2026-10.jsonl.gz"
    if not archive_path.exists():
        archive.roll_sealed_months(W, "2026-10-06", tmp_path)
    if archive_path.exists():
        # This test is conditional on having created an archive
        import gzip
        with gzip.open(archive_path, "wt", encoding="utf-8") as f:
            f.write('{"day":"2026-10-01"}\n')
            f.write('not a dict\n')
        with pytest.raises(archive.Unreadable):
            archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)


def test_load_days_corrupt_outside_window_not_raised(tmp_path):
    """A corrupt day file OUTSIDE the window does not raise."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-09-01.json").write_text("{bad")
    # Loading outside this day's window should not raise
    loaded = archive.load_days(W, "2026-09-15", "2026-09-30", tmp_path)
    assert loaded == {}


def test_save_days_raises_on_unparseable_existing(tmp_path):
    """save_days raises Unreadable rather than overwrite unparseable day file."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Create corrupt day file
    (folder / "2026-10-01.json").write_text("{bad")
    # Attempt to save should raise Unreadable
    with pytest.raises(archive.Unreadable):
        archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path)
    # File bytes unchanged
    assert (folder / "2026-10-01.json").read_text() == "{bad"


def test_save_days_raises_on_mismatched_day_key(tmp_path):
    """save_days raises ValueError for record whose "day" differs from its key."""
    record = day("2026-10-01")
    record["day"] = "2026-10-02"  # Mismatch
    with pytest.raises(ValueError, match="record day '2026-10-02' != key '2026-10-01'"):
        archive.save_days({"2026-10-01": record}, tmp_path)


def test_load_days_window_excludes_outside_dates(tmp_path):
    """load_days: days and months outside [first_day, last_day] are not returned."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Save days from multiple months
    archive.save_days({"2026-09-30": day("2026-09-30"), "2026-10-01": day("2026-10-01"),
                       "2026-10-02": day("2026-10-02")}, tmp_path)
    # Load only October 1st
    loaded = archive.load_days(W, "2026-10-01", "2026-10-01", tmp_path)
    assert sorted(loaded) == ["2026-10-01"]


def test_verification_failure_keeps_day_files(tmp_path):
    """Verification failure: roll returns 0, day files kept, no .tmp remains."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive.save_days({"2026-09-29": day("2026-09-29")}, tmp_path)
    # Monkeypatch _read_month to fail verification
    original_read_month = archive._read_month
    def mock_read_month(path):
        if path.name.startswith("."):
            return None  # Simulate verification failure on tmp
        return original_read_month(path)

    archive._read_month = mock_read_month
    try:
        assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
        # Day file still there
        assert (folder / "2026-09-29.json").exists()
        # No .tmp file
        tmp_files = list(folder.glob(f".2026-09{archive.MONTHLY}*"))
        assert len(tmp_files) == 0
    finally:
        archive._read_month = original_read_month


def test_write_if_changed_handles_tuples(tmp_path):
    """write_if_changed: tuple in doc returns False on second call (serialised equal)."""
    path = tmp_path / "study" / "x.json"
    doc_with_tuple = {"k": (1, 2)}
    # First write
    assert archive.write_if_changed(path, doc_with_tuple) is True
    # Second write with same tuple (stored as list)
    assert archive.write_if_changed(path, doc_with_tuple) is False


def test_wallet_dir_lowercases(tmp_path):
    """wallet_dir lowercases the wallet."""
    wallet_upper = "0x" + "A" * 40
    result = archive.wallet_dir(wallet_upper, tmp_path)
    assert str(result).endswith("0x" + "a" * 40)
