# tests/test_newborn.py
"""Account age from the leaderboard's window volumes, no per-wallet calls."""

from datetime import UTC, datetime

from src import newborn as nb


def _row(addr, value, day, week, month, all_time, name=None):
    return {"ethAddress": addr, "accountValue": str(value), "displayName": name,
            "windowPerformances": [["day", {"vlm": str(day), "pnl": "0"}],
                                   ["week", {"vlm": str(week), "pnl": "0"}],
                                   ["month", {"vlm": str(month), "pnl": "0"}],
                                   ["allTime", {"vlm": str(all_time), "pnl": "1"}]]}


def test_age_class_from_equal_volumes():
    assert nb.age_class(_row("0xa", 1, 1, 100, 100, 100)) == "week"
    assert nb.age_class(_row("0xa", 1, 1, 50, 100, 100)) == "month"
    assert nb.age_class(_row("0xa", 1, 1, 50, 100, 5000)) is None
    assert nb.age_class(_row("0xa", 1, 0, 0, 0, 0)) is None
    assert nb.age_class({"ethAddress": "0xa"}) is None


def test_newborn_rows_filter_by_value_and_sort_youngest_richest_first():
    rows = [_row("0xOld", 5_000_000, 1, 1, 1, 99),
            _row("0xSmall", 500, 1, 1, 1, 1),
            _row("0xMonth", 3_000_000, 1, 10, 100, 100),
            _row("0xWeek", 2_000_000, 1, 100, 100, 100, name="fresh"),
            _row("0xWeekBig", 9_000_000, 1, 7, 7, 7)]
    got = nb.newborn_rows(rows, min_value_usd=1_000_000)
    assert [(g["wallet"], g["age"]) for g in got] == [
        ("0xweekbig", "week"), ("0xweek", "week"), ("0xmonth", "month")]
    assert got[1]["display_name"] == "fresh"


def test_first_appearances_track_new_addresses():
    seen, new = nb.first_appearances([{"ethAddress": "0xA"}, {"ethAddress": "0xB"}],
                                     {"0xa": "earlier"}, "now")
    assert new == ["0xb"] and seen == {"0xa": "earlier", "0xb": "now"}


def test_report_flags_first_seen_newborns():
    rows = [_row("0xNew", 2_000_000, 1, 5, 5, 5), _row("0xKnown", 2_000_000, 1, 5, 5, 5)]
    report, seen = nb.build_report(rows, {"0xknown": "before"},
                                   now=datetime(2026, 9, 10, tzinfo=UTC))
    flags = {r["wallet"]: r["first_seen_this_run"] for r in report["newborn"]}
    assert flags == {"0xnew": True, "0xknown": False}
    assert report["first_seen_this_run"] == 1 and seen["0xnew"].startswith("2026-09-10")
