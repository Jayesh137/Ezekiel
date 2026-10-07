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


# F1: IOC-dominant wallets need measured offsets to decide style
def test_f1_ioc_dominant_without_measured_offsets_is_undecidable():
    ioc_no_offsets = tooling.profile(habits(1000, ioc=990, seen=0, hit=0), 1000, 1000)
    assert tooling.style(ioc_no_offsets) is None


def test_f1_ioc_dominant_with_49_offsets_is_still_undecidable():
    ioc_few_offsets = tooling.profile(habits(1000, ioc=990, seen=49, hit=49), 1000, 1000)
    assert tooling.style(ioc_few_offsets) is None


def test_f1_ioc_dominant_with_50_offsets_is_decidable():
    ioc_50_offsets = tooling.profile(habits(1000, ioc=990, seen=50, hit=50), 1000, 1000)
    assert tooling.style(ioc_50_offsets)["style"] == "PROGRAM_IOC5"


def test_f1_maker_bot_style_decided_without_offsets():
    maker_no_offsets = tooling.profile(habits(2000, alo=2000, cloid=2000), 0, 2000)
    assert tooling.style(maker_no_offsets)["style"] == "MAKER"


def test_f1_t1_returns_insufficient_when_style_undecidable():
    ioc_undecidable = tooling.profile(habits(1000, ioc=990, seen=0, hit=0), 1000, 1000)
    t1 = tooling.t1_style(ioc_undecidable, HIS, HIS)
    assert t1["status"] == "insufficient"


def test_f1_measure_snapshot_with_empty_fills_returns_none_style():
    t0 = 1_790_899_200_000
    # 120 IOC orders but no entries to compute first prices
    fills = program_fills(t0, 120, "BTC", "0.1")
    snap = tooling.measure_snapshot(fills, [])
    # With empty entries, first_prices will be empty, so ioc_offset_seen will be 0
    assert snap["style"] is None


# F2: against_traits needs 1000+ orders in his_all
def test_f2_against_traits_empty_with_short_his_all():
    short_his_all = tooling.profile(habits(5, ioc=0, alo=0, fm=0, cloid=0, trigger=0), 0, 5)
    maker_bot = tooling.profile(habits(500, alo=500, cloid=500), 0, 500)
    t1 = tooling.t1_style(maker_bot, maker_bot, short_his_all)
    assert t1["detail"]["against_traits"] == []


def test_f2_against_traits_populated_with_sufficient_his_all():
    # his_all with 1000+ orders, 0% maker
    his_1000 = tooling.profile(habits(1000, ioc=1000, fm=0, seen=1000, hit=1000), 1000, 1000)
    # candidate with 100% maker
    maker_bot = tooling.profile(habits(500, alo=500, cloid=500), 0, 500)
    t1 = tooling.t1_style(maker_bot, maker_bot, his_1000)
    assert "maker" in t1["detail"]["against_traits"]


# F3: taker is in SHARE_KEYS and None-safe
def test_f3_taker_in_shares_and_none_safe():
    prof_no_orders = tooling.profile(habits(100, ioc=100, seen=100, hit=100), orders=0)
    shares = {k: (round(prof_no_orders[k], 4) if prof_no_orders[k] is not None else None) for k in tooling.SHARE_KEYS}
    assert shares["taker"] is None
    assert "taker" in tooling.SHARE_KEYS


# F4: insufficient T1 carries "short" field
def test_f4_insufficient_t1_says_short_candidate():
    few = tooling.profile(habits(99, ioc=99, seen=99, hit=99), 99, 99)
    t1 = tooling.t1_style(few, HIS, HIS)
    assert t1["status"] == "insufficient" and t1["detail"]["short"] == "candidate"


def test_f4_insufficient_t1_says_short_his():
    enough_candidate = tooling.profile(habits(100, ioc=100, seen=100, hit=100), 100, 100)
    t1 = tooling.t1_style(enough_candidate, None, HIS)
    assert t1["status"] == "insufficient" and t1["detail"]["short"] == "his"


# F5: snapshot_signature does not invent readings
def test_f5_snapshot_signature_none_input():
    sig = tooling.snapshot_signature(None)
    assert sig == {"clip_table": {}, "clip_notionals": {}, "program_runs": None, "ioc_5pct_share": None}


def test_f5_snapshot_signature_empty_input():
    sig = tooling.snapshot_signature({})
    assert sig == {"clip_table": {}, "clip_notionals": {}, "program_runs": None, "ioc_5pct_share": None}


def test_f5_snapshot_signature_no_invented_share_count():
    snap = {"clip_table": {"BTC": 0.1}, "clip_notionals": {"BTC": 1000.0}, "program_runs": 5, "ioc5": 0.95}
    sig = tooling.snapshot_signature(snap)
    assert sig["clip_table"]["BTC"]["share"] is None
    assert sig["clip_table"]["BTC"]["count"] is None
    assert sig["program_runs"] == 5


# F6: spec rule tests
def test_f6_parity_with_execution_program():
    from src import execution_program as ep
    t0 = 1_790_899_200_000
    fills = (program_fills(t0, 30, "ZEC", "1.0") +
             program_fills(t0 + 300_000, 7, "BTC", "0.1") +  # under MIN_CLIP_ORDERS (7 < 8)
             program_fills(t0 + 600_000, 50, "ETH", "0.05") +  # 50 orders at 0.05
             program_fills(t0 + 686_500, 30, "ETH", "0.06"))  # + 30 at 0.06 after the first batch
    days = {}
    times = [f["time"] for f in fills]
    records.fold_fills(days, fills, wallet=W, role="studied", start_ms=min(times),
                       end_ms=max(times) + 1, last_fill_ms=None)
    summary = tooling.summarise(list(days.values()))
    summary_sig = tooling.clip_signature(summary)
    direct_sig = ep.signature(fills, [])
    # Both should have ZEC; both should lack BTC (7 < MIN_CLIP_ORDERS) and ETH (share < MIN_CLIP_SHARE)
    assert set(summary_sig["clip_table"].keys()) == set(direct_sig["clip_table"].keys())
    assert "ZEC" in summary_sig["clip_table"]
    assert "BTC" not in summary_sig["clip_table"]
    assert "ETH" not in summary_sig["clip_table"]


def test_f6_his_recent_vs_his_all_difference():
    # recent has IOC 100%, all_mixed has IOC 50%, GTC 30%, FM 20%
    recent_ioc = tooling.profile(habits(100, ioc=100, seen=100, hit=100), 100, 100)
    all_mixed = tooling.profile(habits(1000, ioc=500, gtc=300, fm=200, seen=500, hit=500), 1000, 1000)
    # maker_bot has 100% ALO and client_ids
    maker_bot = tooling.profile(habits(500, alo=500, cloid=500), 0, 500)
    # When compared to all_mixed (has 30% GTC/maker), maker has maker trait; against
    # When compared to recent_ioc (0% maker), maker has maker trait; against
    t1_recent = tooling.t1_style(maker_bot, recent_ioc, all_mixed)
    t1_all = tooling.t1_style(maker_bot, recent_ioc, recent_ioc)
    # all_mixed has 1000 orders so computes against; recent_ioc has only 100 so doesn't
    assert len(t1_recent["detail"]["against_traits"]) > 0
    assert len(t1_all["detail"]["against_traits"]) == 0


def test_f6_mixed_style():
    mixed = tooling.profile(habits(100, ioc=40, gtc=30, fm=30), 40, 100)
    assert tooling.style(mixed)["style"] == "MIXED"


def test_f6_trigger_flag_threshold():
    at_2pct = tooling.profile(habits(1000, ioc=1000, trigger=20, seen=1000, hit=1000), 1000, 1000)
    below_1pct = tooling.profile(habits(1000, ioc=1000, trigger=5, seen=1000, hit=1000), 1000, 1000)
    assert tooling.style(at_2pct)["flags"]["triggers"] is True
    assert tooling.style(below_1pct)["flags"]["triggers"] is False


def test_f6_summarise_empty():
    summary = tooling.summarise([])
    assert summary["days"] == 0 and summary["covered_days"] == 0 and summary["orders"] == 0
    assert summary["taker_orders"] == 0 and summary["program_runs"] == 0
    assert summary["habits"] is None
    assert sum(summary["cadence"]) == 0 and len(summary["cadence"]) == records.CADENCE_BINS
