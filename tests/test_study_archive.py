"""The study's files: daily records, sealed months, state, and nothing rewritten needlessly."""

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
    assert sorted(archive.load_days(W, "2026-09-01", "2026-09-30", tmp_path)) == [
        "2026-09-29", "2026-09-30"]


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
