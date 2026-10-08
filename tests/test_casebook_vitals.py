"""One portfolio read is a suspect's vitals and its life story (spec §8)."""

from src.casebook import vitals

DAY = 86_400_000
NOW = 1_791_419_654_475


def window(points, vlm):
    return {"accountValueHistory": [[ts, str(v)] for ts, v in points], "pnlHistory": [], "vlm": str(vlm)}


def portfolio(points, day=0.0, week=0.0, month=0.0, all_time=0.0):
    return [["day", window(points[-2:], day)], ["week", window(points[-4:], week)],
            ["month", window(points, month)], ["allTime", window(points, all_time)],
            ["perpDay", window(points[-2:], day)], ["perpAllTime", window(points, all_time)]]


UNKNOWN = portfolio([(NOW - i * DAY, 0.0) for i in range(10, -1, -1)])


def test_an_address_hyperliquid_does_not_know():
    reading = vitals.parse_portfolio(UNKNOWN)
    assert reading["on_hl"] is False and reading["birth_ms"] is None
    assert reading["total_value"] == 0.0 and reading["month_volume"] == 0.0


def test_a_live_account_has_a_birth_volumes_and_a_bounded_life():
    points = [(NOW - (200 - i) * DAY, 0.0 if i < 5 else 1000.0 * i) for i in range(201)]
    reading = vitals.parse_portfolio(portfolio(points, day=5.0, week=6.0, month=7.0, all_time=8.0))
    assert reading["on_hl"] is True and reading["birth_ms"] == points[5][0]
    assert reading["total_value"] == 200_000.0
    assert (reading["day_volume"], reading["week_volume"], reading["month_volume"],
            reading["all_time_volume"]) == (5.0, 6.0, 7.0, 8.0)
    assert len(reading["life"]) == vitals.LIFE_POINTS
    assert reading["life"][0][0] == points[0][0] and reading["life"][-1][0] == points[-1][0]


def test_a_payload_that_is_not_a_portfolio_is_no_reading():
    assert vitals.parse_portfolio({"error": "x"}) is None
    assert vitals.parse_portfolio([]) is None
    assert vitals.parse_portfolio([["nonsense", 1]]) is None


def test_a_first_probe_is_a_baseline_not_a_storm():
    old_big = portfolio([(NOW - 300 * DAY, 5e6), (NOW - DAY, 5e7), (NOW, 5e7)], week=1e6, month=1e7)
    assert vitals.wake_events(None, vitals.parse_portfolio(old_big), NOW) == []
    newborn = portfolio([(NOW - 3 * DAY, 0.0), (NOW - 2 * DAY, 999.0), (NOW, 2e6)], week=1e5, month=1e5)
    kinds = [e["kind"] for e in vitals.wake_events(None, vitals.parse_portfolio(newborn), NOW)]
    assert kinds == ["hl_opened"]
    grew = portfolio([(NOW - 90 * DAY, 100.0), (NOW - 8 * DAY, 100.0), (NOW, 3e6)], week=1e5, month=1e5)
    assert [e["kind"] for e in vitals.wake_events(None, vitals.parse_portfolio(grew), NOW)] == ["hl_grew"]


def test_wake_events_against_the_previous_probe():
    quiet = {"on_hl": True, "total_value": 50_000.0, "month_volume": 0.0, "week_volume": 0.0}
    woke = vitals.parse_portfolio(portfolio([(NOW - 90 * DAY, 5e4), (NOW, 5e4)], week=10.0, month=10.0))
    assert [e["kind"] for e in vitals.wake_events(quiet, woke, NOW)] == ["hl_woke"]
    absent = {"on_hl": False, "total_value": 0.0, "month_volume": 0.0, "week_volume": 0.0}
    opened = vitals.parse_portfolio(portfolio([(NOW - DAY, 10.0), (NOW, 2e6)]))
    assert [e["kind"] for e in vitals.wake_events(absent, opened, NOW)] == ["hl_opened"]
    small = {"on_hl": True, "total_value": 100.0, "month_volume": 5.0, "week_volume": 5.0}
    big = vitals.parse_portfolio(portfolio([(NOW - 9 * DAY, 100.0), (NOW, 2e6)], week=5.0, month=5.0))
    assert [e["kind"] for e in vitals.wake_events(small, big, NOW)] == ["hl_grew"]
    rich = {"on_hl": True, "total_value": 5e5, "month_volume": 5.0, "week_volume": 5.0}
    empty = vitals.parse_portfolio(portfolio([(NOW - 9 * DAY, 5e5), (NOW, 3.0)], week=5.0, month=5.0))
    assert [e["kind"] for e in vitals.wake_events(rich, empty, NOW)] == ["hl_emptied"]


def test_an_unknown_previous_volume_is_not_a_quiet_one():
    unknown = {"on_hl": True, "total_value": 5e4, "month_volume": None, "week_volume": None}
    woke = vitals.parse_portfolio(portfolio([(NOW - 9 * DAY, 5e4), (NOW, 5e4)], week=10.0, month=10.0))
    assert vitals.wake_events(unknown, woke, NOW) == []


def test_apply_probe_keeps_one_row_a_day():
    case = {"address": "0x" + "a" * 40, "hl": {}}
    reading = vitals.parse_portfolio(portfolio([(NOW - 9 * DAY, 5e4), (NOW, 5e4)], week=1.0, month=1.0))
    assert vitals.apply_probe(case, reading, now_ms=NOW) == []
    before = {k: case["hl"][k] for k in ("total_value", "on_hl", "life", "probes")}
    assert vitals.apply_probe(case, None, now_ms=NOW + 1000, error="rate limited") == []
    assert case["hl"]["probe_ok"] is False and case["hl"]["probe_error"] == "rate limited"
    assert {k: case["hl"][k] for k in before} == before
    vitals.apply_probe(case, reading, now_ms=NOW + 2000)
    assert len(case["hl"]["probes"]) == 1 and case["hl"]["probe_ok"] is True
    assert "probe_error" not in case["hl"]


def test_a_failed_probe_changes_nothing():
    case = {"address": "0x" + "a" * 40, "hl": {}}
    assert vitals.apply_probe(case, None, now_ms=NOW, error="ok: false") == []
    assert case["hl"]["probe_ok"] is False and "last_ok_at" not in case["hl"]
    assert "total_value" not in case["hl"]


def test_probe_rows_are_bounded():
    case = {"address": "0x" + "a" * 40, "hl": {}}
    reading = vitals.parse_portfolio(portfolio([(NOW - 9 * DAY, 5e4), (NOW, 5e4)]))
    for i in range(vitals.MAX_PROBES + 200):
        vitals.apply_probe(case, reading, now_ms=NOW + i * DAY)
    assert vitals.MAX_PROBES <= len(case["hl"]["probes"]) < vitals.MAX_PROBES + 12
