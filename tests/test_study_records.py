"""Daily records: additive, never counting a fill twice, never claiming what was not read."""

from src.study import records as rec

W = "0x" + "a" * 40
DAY0 = 1_790_899_200_000  # 2026-10-02 00:00:00 UTC
HOUR = rec.HOUR_MS


def fill(t, coin="BTC", side="B", sz="0.1", px="100.0", crossed=True):
    return {"coin": coin, "side": side, "sz": sz, "px": px, "time": t,
            "crossed": crossed, "oid": t, "tid": t}


def program(start, n, gap_ms=1_700, coin="BTC", side="B"):
    return [fill(start + i * gap_ms, coin, side) for i in range(n)]


def order(t, tif="Ioc", side="B", px="105.0", cloid=None, status="filled",
          trigger=False, otype="Limit"):
    return {"order": {"coin": "BTC", "side": side, "limitPx": px, "oid": t, "timestamp": t,
                      "tif": tif, "cloid": cloid, "isTrigger": trigger, "orderType": otype,
                      "reduceOnly": False}, "status": status}


def test_minutes_round_trip():
    assert rec.decode_minutes(rec.encode_minutes({0, 7, 8, 1439})) == {0, 7, 8, 1439}
    assert rec.decode_minutes(None) == set()


def test_a_program_run_is_counted_once_with_its_cadence():
    days = {}
    fills = program(DAY0 + HOUR, 20)
    last = rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                          end_ms=DAY0 + 2 * HOUR, last_fill_ms=None)
    day = days["2026-10-02"]
    assert (day["fills"], day["orders"], day["taker_orders"], day["program_runs"]) == (20, 20, 20, 1)
    assert [r["n"] for r in day["runs"]] == [20] and day["runs"][0]["clip"] == 0.1
    assert day["cadence"][17] == 19 and sum(day["cadence"]) == 19
    assert [d[3] for d in day["decisions"]] == ["run", "session"]
    assert rec.decode_minutes(day["minutes"]) == {60}
    assert day["coverage"]["fills"] == [[DAY0, DAY0 + 2 * HOUR]]
    assert last == fills[-1]["time"]


def test_an_overlapping_reread_never_counts_a_fill_twice():
    fills = program(DAY0 + HOUR, 20) + program(DAY0 + 3 * HOUR, 20, coin="ETH")
    once = {}
    rec.fold_fills(once, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 4 * HOUR, last_fill_ms=None)
    twice = {}
    mid = DAY0 + 2 * HOUR
    last = rec.fold_fills(twice, fills[:20], wallet=W, role="studied", start_ms=DAY0,
                          end_ms=mid, last_fill_ms=None)
    # The second read re-delivers every fill; only [mid, end) may be folded.
    rec.fold_fills(twice, fills, wallet=W, role="studied", start_ms=mid,
                   end_ms=DAY0 + 4 * HOUR, last_fill_ms=last)
    assert twice == once


def test_duplicate_rows_inside_one_batch_count_once():
    days = {}
    f = fill(DAY0 + HOUR)
    rec.fold_fills(days, [f, dict(f)], wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 2 * HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["fills"] == 1


def test_the_boundary_never_cuts_through_a_run():
    now = DAY0 + 10 * HOUR
    run = [now - 6 * 60_000 + i * 1_700 for i in range(200)]  # crosses now - 5 min
    boundary, split = rec.quiet_boundary([now - HOUR] + run, DAY0, now)
    assert (boundary, split) == (run[0], False)


def test_a_quiet_tail_commits_up_to_five_minutes_before_now():
    now = DAY0 + 10 * HOUR
    assert rec.quiet_boundary([now - 30 * 60_000], DAY0, now) == (now - rec.BOUNDARY_LAG_MS, False)


def test_continuous_quoting_falls_back_to_the_hour_mark():
    now = DAY0 + 10 * HOUR + 20 * 60_000
    times = list(range(DAY0 + 7 * HOUR, now, 5_000))
    assert rec.quiet_boundary(times, DAY0 + 7 * HOUR, now) == (DAY0 + 10 * HOUR, True)


def test_nothing_new_to_commit_leaves_the_cursor():
    now = DAY0 + HOUR
    assert rec.quiet_boundary([], now - 60_000, now) == (now - 60_000, False)


def test_a_run_crossing_midnight_belongs_to_its_start_day():
    fills = program(DAY0 + rec.DAY_MS - 30_000, 40)  # 23:59:30, ends 00:00:36
    days = {}
    rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + 2 * rec.DAY_MS, last_fill_ms=None)
    first, second = days["2026-10-02"], days["2026-10-03"]
    assert (first["orders"], second["orders"]) == (18, 22)
    assert [r["n"] for r in first["runs"]] == [40] and second["runs"] == []
    assert first["coverage"]["fills"] == [[DAY0, DAY0 + rec.DAY_MS]]
    assert second["coverage"]["fills"] == [[DAY0 + rec.DAY_MS, DAY0 + 2 * rec.DAY_MS]]


def test_a_session_start_is_never_assumed_before_the_read():
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 10 * 60_000)], wallet=W, role="studied",
                   start_ms=DAY0, end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["decisions"] == []
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 40 * 60_000)], wallet=W, role="studied",
                   start_ms=DAY0, end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert [d[3] for d in days["2026-10-02"]["decisions"]] == ["session"]


def test_saturation_is_marked_on_the_first_day_read():
    days = {}
    rec.fold_fills(days, [], wallet=W, role="studied", start_ms=DAY0 + HOUR,
                   end_ms=DAY0 + rec.DAY_MS + HOUR, last_fill_ms=None, saturated=True)
    assert days["2026-10-02"]["coverage"]["saturated"] is True
    assert days["2026-10-03"]["coverage"]["saturated"] is False


def test_more_than_twenty_coins_fold_the_tail_into_other():
    fills = [fill(DAY0 + i * 40_000, coin=f"C{i:02d}") for i in range(25)]
    days = {}
    rec.fold_fills(days, fills, wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + rec.DAY_MS, last_fill_ms=None)
    coins = days["2026-10-02"]["coins"]
    assert len(coins) == rec.MAX_COINS
    assert coins[rec.OTHER]["orders"] == 25 - (rec.MAX_COINS - 1)
    assert sum(c["orders"] for c in coins.values()) == 25


def test_orders_fold_habits_offsets_and_web_clicks():
    t = DAY0 + HOUR
    entries = [order(t), order(t + 1, tif="FrontendMarket", otype="Market", px="0"),
               order(t + 2, tif="Alo", cloid="0x01", status="canceled"),
               order(t + 3, tif=None, trigger=True, otype="Stop Market", status="open")]
    days = {}
    rec.fold_orders(days, entries, {str(t): 100.0}, wallet=W, role="studied",
                    start_ms=DAY0, end_ms=DAY0 + rec.DAY_MS)
    day = days["2026-10-02"]
    h = day["habits"]
    assert h["orders_seen"] == 4
    assert h["tif"] == {"Ioc": 1, "Gtc": 0, "Alo": 1, "FrontendMarket": 1, "other": 1}
    assert (h["cloid"], h["trigger"], h["canceled"], h["open"]) == (1, 1, 1, 1)
    assert (h["ioc_offset_seen"], h["ioc_offset_5pct"]) == (1, 1)
    assert rec.decode_minutes(day["manual_minutes"]) == {60}
    assert [d[3] for d in day["decisions"]] == ["manual"]


def test_a_day_no_order_read_covered_keeps_its_habits_unknown():
    days = {}
    rec.fold_fills(days, [fill(DAY0 + 60_000)], wallet=W, role="studied", start_ms=DAY0,
                   end_ms=DAY0 + HOUR, last_fill_ms=None)
    assert days["2026-10-02"]["habits"] is None
    assert days["2026-10-02"]["manual_minutes"] is None
    assert days["2026-10-02"]["ledger"] is None


def test_ledger_rows_are_kept_once():
    row = {"time": DAY0 + 5, "hash": "0xh", "delta": {"type": "deposit", "usdc": "1000.0"}}
    days = {}
    for _ in range(2):
        rec.fold_ledger(days, [row], wallet=W, role="studied", start_ms=DAY0,
                        end_ms=DAY0 + rec.DAY_MS)
    assert days["2026-10-02"]["ledger"] == [
        {"ts_ms": DAY0 + 5, "type": "deposit", "usd": 1000.0, "hash": "0xh"}]


def test_covered_day_needs_twenty_hours():
    day = rec.empty_day(W, "2026-10-02", "studied")
    day["coverage"]["fills"] = [[DAY0, DAY0 + 19 * HOUR]]
    assert not rec.covered_day(day)
    day["coverage"]["fills"] = [[DAY0, DAY0 + 21 * HOUR]]
    assert rec.covered_day(day)
