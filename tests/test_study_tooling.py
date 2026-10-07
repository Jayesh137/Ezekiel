"""Tooling tests: how a wallet's orders are made, compared with his."""

from src.study import records, tooling

W = "0x" + "d" * 40


def habits(n, ioc=0, gtc=0, alo=0, fm=0, cloid=0, trigger=0, seen=0, hit=0):
    h = records.empty_habits()
    h["orders_seen"] = n
    h["tif"].update(Ioc=ioc, Gtc=gtc, Alo=alo, FrontendMarket=fm)
    h["tif"]["other"] = n - ioc - gtc - alo - fm
    h.update(cloid=cloid, trigger=trigger, ioc_offset_seen=seen, ioc_offset_5pct=hit)
    return h


HIS = tooling.profile(habits(1000, ioc=988, fm=12, seen=988, hit=987), 1000, 1000)
MAKER_BOT = tooling.profile(habits(2000, alo=2000, cloid=2000), 0, 2000)
WEB_TRADER = tooling.profile(habits(500, fm=400, gtc=100), 400, 500)


def test_styles():
    assert tooling.style(HIS) == {"style": "PROGRAM_IOC5", "flags": {
        "client_ids": False, "triggers": False, "maker": False}}
    assert tooling.style(MAKER_BOT)["style"] == "MAKER"
    assert tooling.style(MAKER_BOT)["flags"]["client_ids"] is True
    assert tooling.style(WEB_TRADER)["style"] == "MANUAL_UI"


def test_t1_a_maker_bot_with_client_ids_carries_traits_he_never_shows():
    t1 = tooling.t1_style(MAKER_BOT, HIS, HIS)
    assert t1["status"] == "measured" and t1["statistic"] is False
    assert t1["detail"]["against_traits"] == ["client_ids", "maker"]


def test_t1_a_web_ui_trader_differs_but_shows_no_trait_he_never_shows():
    t1 = tooling.t1_style(WEB_TRADER, HIS, HIS)
    assert t1["statistic"] is False and t1["detail"]["against_traits"] == []


def test_t1_he_matches_himself():
    assert tooling.t1_style(HIS, HIS, HIS)["statistic"] is True


def test_t1_needs_a_hundred_orders():
    few = tooling.profile(habits(99, ioc=99, seen=99, hit=99), 99, 99)
    assert tooling.t1_style(few, HIS, HIS)["status"] == "insufficient"
    assert tooling.t1_style(None, HIS, HIS)["status"] == "insufficient"


def test_wasserstein_in_seconds():
    a = [0] * records.CADENCE_BINS
    b = [0] * records.CADENCE_BINS
    a[16], b[17] = 100, 100
    assert tooling.wasserstein(a, a) == 0.0
    assert tooling.wasserstein(a, b) == 0.1
    assert tooling.wasserstein(a, [0] * records.CADENCE_BINS) is None


def test_t2_needs_two_hundred_gaps():
    hist = [0] * records.CADENCE_BINS
    hist[17] = 199
    assert tooling.t2_rhythm(hist, hist)["status"] == "insufficient"
    hist[17] = 200
    t2 = tooling.t2_rhythm(hist, hist)
    assert (t2["status"], t2["statistic"], t2["n"]) == ("measured", 0.0, 200)


def program_fills(start, n, coin, sz, px="100.0"):
    return [{"coin": coin, "side": "A", "sz": sz, "px": px, "time": start + i * 1_700,
             "crossed": True, "oid": start + i * 1_700, "tid": start + i * 1_700}
            for i in range(n)]


def summary_of(fills):
    days = {}
    times = [f["time"] for f in fills]
    records.fold_fills(days, fills, wallet=W, role="studied", start_ms=min(times),
                       end_ms=max(times) + 1, last_fill_ms=None)
    return tooling.summarise(list(days.values()))


def test_t3_a_wallet_running_his_clip_table_matches_it():
    t0 = 1_790_899_200_000
    his = summary_of(program_fills(t0, 30, "ZEC", "1.0") + program_fills(t0 + 600_000, 30, "BTC", "0.1")
                     + program_fills(t0 + 1_200_000, 30, "NEAR", "250.0"))
    same = summary_of(program_fills(t0, 20, "ZEC", "1.0") + program_fills(t0 + 600_000, 20, "BTC", "0.1")
                      + program_fills(t0 + 1_200_000, 20, "NEAR", "250.0"))
    t3 = tooling.t3_clips(tooling.clip_signature(same), tooling.clip_signature(his))
    assert t3["status"] == "measured" and t3["statistic"] >= 1.0
    other = summary_of(program_fills(t0, 20, "SOL", "3.0"))
    assert tooling.t3_clips(tooling.clip_signature(other),
                            tooling.clip_signature(his))["status"] == "insufficient"


def test_a_snapshot_measures_style_rhythm_and_clips_from_raw_reads():
    t0 = 1_790_899_200_000
    fills = program_fills(t0, 120, "BTC", "0.1")
    entries = [{"order": {"coin": "BTC", "side": "A", "limitPx": "95.0", "oid": f["oid"],
                          "timestamp": f["time"], "tif": "Ioc", "cloid": None,
                          "isTrigger": False, "orderType": "Limit", "reduceOnly": False},
                "status": "filled"} for f in fills]
    snap = tooling.measure_snapshot(fills, entries)
    assert snap["orders_seen"] == 120 and snap["style"]["style"] == "PROGRAM_IOC5"
    assert sum(snap["cadence"]) == 119 and snap["clip_table"] == {"BTC": 0.1}
    sig = tooling.snapshot_signature(snap)
    assert sig["clip_table"]["BTC"]["size"] == 0.1 and sig["program_runs"] == 1
