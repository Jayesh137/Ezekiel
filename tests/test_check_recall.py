"""The recall report is well-formed and honest under missing/thin data."""

from scripts import check_recall


def _fill(oid, t, coin, sz):
    return {"oid": oid, "time": t, "coin": coin, "side": "A", "sz": str(sz),
            "px": "4.0", "crossed": True, "tid": oid}


def test_report_on_no_fills_is_marked_not_a_crash():
    assert check_recall.build_report([], None)["status"] == "no_fills"


def test_report_carries_history_and_recent_sections():
    fills = []
    oid = 1
    for base in range(0, 6_000_000, 100_000):
        for coin, clip in (("NEAR", 250), ("ZEC", 1), ("BTC", 0.1)):
            for _ in range(4):
                fills.append(_fill(oid, base, coin, clip))
                oid += 1
    report = check_recall.build_report(fills, None, now_ms=6_000_000)
    assert "history_split" in report and "recent_migration" in report
    assert report["census_present"] is False
    assert report["history_split"]["recognised_rate"] is not None
