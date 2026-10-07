"""The study's files: daily records, sealed months, state, and nothing rewritten needlessly."""

import gzip

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


def test_corrupt_day_file_in_sealed_month_survives_beside_good_one(tmp_path):
    """A corrupt (truncated JSON) day file in a sealed month survives the roll."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30")}, tmp_path)
    day30_path = folder / "2026-09-30.json"
    day30_path.write_text("{truncated")
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 1
    archive_path = folder / "2026-09.jsonl.gz"
    assert archive_path.exists()
    read_back = archive._read_month(archive_path)
    assert sorted(read_back) == ["2026-09-29"]
    assert day30_path.exists()
    assert day30_path.read_text() == "{truncated"


def test_all_day_files_of_sealed_month_unreadable_no_archive(tmp_path):
    """Every day file of a sealed month unreadable and no archive written."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive.save_days({"2026-09-29": day("2026-09-29"), "2026-09-30": day("2026-09-30")}, tmp_path)
    (folder / "2026-09-29.json").write_text("{bad")
    (folder / "2026-09-30.json").write_text("{bad")
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
    assert not (folder / "2026-09.jsonl.gz").exists()
    assert (folder / "2026-09-29.json").exists()
    assert (folder / "2026-09-30.json").exists()


def test_load_state_strict_reading(tmp_path):
    """load_state: missing → {}; truncated → Unreadable; list → Unreadable."""
    assert archive.load_state(tmp_path) == {}
    state_path = archive.root(tmp_path) / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("{bad")
    with pytest.raises(archive.Unreadable):
        archive.load_state(tmp_path)
    state_path.write_text("[]")
    with pytest.raises(archive.Unreadable):
        archive.load_state(tmp_path)


def test_load_days_truncated_day_file(tmp_path):
    """load_days raises Unreadable for truncated day file inside window."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-10-01.json").write_text("{bad")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)


def test_load_days_wrong_day_field(tmp_path):
    """load_days raises Unreadable for day file with wrong "day" field."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-10-01.json").write_text('{"day":"2026-10-02"}')
    with pytest.raises(archive.Unreadable, match="not the record for"):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)


def test_load_days_zero_byte_archive(tmp_path):
    """load_days raises Unreadable for zero-byte month archive inside window."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-10.jsonl.gz").write_bytes(b"")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)


def test_load_days_corrupted_gzip(tmp_path):
    """load_days raises Unreadable for corrupted gzip (zlib error)."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-10.jsonl.gz").write_bytes(b"\x1f\x8b\x08\x00" + b"bad gzip data")
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-10-01", "2026-10-31", tmp_path)


def test_load_days_non_dict_row(tmp_path):
    """load_days raises Unreadable for month archive with non-dict row."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive_path = folder / "2026-09.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8") as f:
        f.write('{"day":"2026-09-29"}\n')
        f.write('[1, 2]\n')
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-09-01", "2026-09-30", tmp_path)


def test_load_days_empty_day_field(tmp_path):
    """load_days raises Unreadable for row with empty "day" field."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive_path = folder / "2026-09.jsonl.gz"
    with gzip.open(archive_path, "wt", encoding="utf-8") as f:
        f.write('{"day":"2026-09-29"}\n')
        f.write('{"day":""}\n')
    with pytest.raises(archive.Unreadable):
        archive.load_days(W, "2026-09-01", "2026-09-30", tmp_path)


def test_load_days_corrupt_outside_window_not_raised(tmp_path):
    """A corrupt day file OUTSIDE the window does not raise."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-09-01.json").write_text("{bad")
    loaded = archive.load_days(W, "2026-09-15", "2026-09-30", tmp_path)
    assert loaded == {}


def test_load_days_archives_outside_window_not_opened(tmp_path):
    """load_days: month archives outside window are not opened (no corrupt raises)."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Create archives for 2026-08 and 2026-09
    archive_08 = folder / "2026-08.jsonl.gz"
    with gzip.open(archive_08, "wt", encoding="utf-8") as f:
        f.write('{"day":"2026-08-30"}\n')
        f.write('{"day":"2026-08-31"}\n')
    archive_09 = folder / "2026-09.jsonl.gz"
    with gzip.open(archive_09, "wt", encoding="utf-8") as f:
        f.write('{"day":"2026-09-01"}\n')
        f.write('{"day":"2026-09-02"}\n')
    # Create corrupt 2026-07 archive outside window
    (folder / "2026-07.jsonl.gz").write_bytes(b"")
    # load_days over 2026-09-02 to 2026-09-30 should return only 2026-09-02
    loaded = archive.load_days(W, "2026-09-02", "2026-09-30", tmp_path)
    assert sorted(loaded) == ["2026-09-02"]


def test_save_days_raises_on_unparseable_existing(tmp_path):
    """save_days raises Unreadable rather than overwrite unparseable day file."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    (folder / "2026-10-01.json").write_text("{bad")
    with pytest.raises(archive.Unreadable):
        archive.save_days({"2026-10-01": day("2026-10-01")}, tmp_path)
    assert (folder / "2026-10-01.json").read_text() == "{bad"


def test_save_days_raises_on_mismatched_day_key(tmp_path):
    """save_days raises ValueError for record whose "day" differs from its key."""
    record = day("2026-10-01")
    record["day"] = "2026-10-02"
    with pytest.raises(ValueError, match="record day '2026-10-02' != key '2026-10-01'"):
        archive.save_days({"2026-10-01": record}, tmp_path)


def test_save_days_all_or_nothing_on_value_error(tmp_path):
    """save_days: ValueError raises before writing anything."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    good_record = day("2026-10-01")
    bad_record = day("2026-10-02")
    bad_record["day"] = "wrong"
    # Attempt to save good and bad together
    with pytest.raises(ValueError):
        archive.save_days({"2026-10-01": good_record, "2026-10-02": bad_record}, tmp_path)
    # Good day should not have been written
    assert not (folder / "2026-10-01.json").exists()


def test_save_days_all_or_nothing_on_unreadable(tmp_path):
    """save_days: Unreadable on existing file raises before writing anything."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Create corrupt existing file for one day
    (folder / "2026-10-02.json").write_text("{bad")
    good_record = day("2026-10-01")
    bad_record = day("2026-10-02")
    # Attempt to save both
    with pytest.raises(archive.Unreadable):
        archive.save_days({"2026-10-01": good_record, "2026-10-02": bad_record}, tmp_path)
    # Good day should not have been written
    assert not (folder / "2026-10-01.json").exists()


def test_write_if_changed_actually_writes_changed(tmp_path):
    """write_if_changed: write changed content (True), unchanged (False)."""
    path = tmp_path / "study" / "x.json"
    assert archive.write_if_changed(path, {"a": 1}) is True
    assert archive.write_if_changed(path, {"a": 2}) is True
    assert archive.write_if_changed(path, {"a": 2}) is False


def test_write_if_changed_normalises_int_keys(tmp_path):
    """write_if_changed: int-keyed dict doesn't rewrite."""
    path = tmp_path / "study" / "x.json"
    doc = {"h": {9: 1, 10: 2}}
    assert archive.write_if_changed(path, doc) is True
    assert archive.write_if_changed(path, doc) is False


def test_save_days_normalises_int_keys_and_tuples(tmp_path):
    """save_days: day with int-keyed dict and tuple doesn't rewrite."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    record = day("2026-10-01")
    record["coverage"]["fills"] = [(1, 2)]
    record["data"] = {9: "a", 10: "b"}
    assert archive.save_days({"2026-10-01": record}, tmp_path) == 1
    assert archive.save_days({"2026-10-01": record}, tmp_path) == 0


def test_roll_keeps_mislabelled_day_file(tmp_path):
    """roll_sealed_months: day file with wrong label is kept and not merged."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    # Save a good day
    archive.save_days({"2026-09-29": day("2026-09-29")}, tmp_path)
    # Replace 2026-09-29.json with content labelled as 2026-09-05
    mislabelled = day("2026-09-05")
    mislabelled["day"] = "2026-09-05"
    archive.write_compact(folder / "2026-09-29.json", mislabelled)
    # Roll
    assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
    # Mislabelled file still on disk
    assert (folder / "2026-09-29.json").exists()
    # Archive was not created (no files were merged)
    assert not (folder / "2026-09.jsonl.gz").exists()


def test_load_state_directory_is_unreadable(tmp_path):
    """load_state: state.json is a directory raises Unreadable."""
    state_path = archive.root(tmp_path) / "state.json"
    state_path.mkdir(parents=True)
    with pytest.raises(archive.Unreadable):
        archive.load_state(tmp_path)


def test_load_days_window_excludes_outside_dates(tmp_path):
    """load_days: days and months outside [first_day, last_day] are not returned."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive.save_days({"2026-09-30": day("2026-09-30"), "2026-10-01": day("2026-10-01"),
                       "2026-10-02": day("2026-10-02")}, tmp_path)
    loaded = archive.load_days(W, "2026-10-01", "2026-10-01", tmp_path)
    assert sorted(loaded) == ["2026-10-01"]


def test_verification_failure_keeps_day_files(tmp_path):
    """Verification failure: roll returns 0, day files kept, no .tmp remains."""
    folder = archive.wallet_dir(W, tmp_path)
    folder.mkdir(parents=True)
    archive.save_days({"2026-09-29": day("2026-09-29")}, tmp_path)
    original_read_month = archive._read_month
    def mock_read_month(path):
        if path.name.startswith("."):
            return None
        return original_read_month(path)

    archive._read_month = mock_read_month
    try:
        assert archive.roll_sealed_months(W, "2026-10-06", tmp_path) == 0
        assert (folder / "2026-09-29.json").exists()
        tmp_files = list(folder.glob(f".2026-09{archive.MONTHLY}*"))
        assert len(tmp_files) == 0
    finally:
        archive._read_month = original_read_month


def test_write_if_changed_handles_tuples(tmp_path):
    """write_if_changed: tuple in doc returns False on second call (serialised equal)."""
    path = tmp_path / "study" / "x.json"
    doc_with_tuple = {"k": (1, 2)}
    assert archive.write_if_changed(path, doc_with_tuple) is True
    assert archive.write_if_changed(path, doc_with_tuple) is False


def test_wallet_dir_lowercases(tmp_path):
    """wallet_dir lowercases the wallet."""
    wallet_upper = "0x" + "A" * 40
    result = archive.wallet_dir(wallet_upper, tmp_path)
    assert str(result).endswith("0x" + "a" * 40)
