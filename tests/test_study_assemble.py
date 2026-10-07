"""Assembly: his reference, his months, the panels, and each wallet's tooling verdict."""

from src.study import assemble, records, verdict

T = "0x45d26f28196d226497130c4bac709d808fed4029"
W, BOT = "0x" + "e" * 40, "0x" + "f" * 40
JUNE = 1_780_272_000_000  # 2026-06-01 00:00:00 UTC
HIS_STYLE = {"style": "PROGRAM_IOC5", "flags": {"client_ids": False, "triggers": False,
                                                "maker": False}}
MAKER = {"style": "MAKER", "flags": {"client_ids": True, "triggers": False, "maker": True}}
THREE_COINS = (("BTC", "0.1"), ("ETH", "1.0"), ("SOL", "10.0"))


def history(wallet, start_ms, n_days, *, tif="Ioc", cloid=None, crossed=True, n=40,
            coins=(("BTC", "0.1"),), gap_ms=1_700):
    """Per day, one run of `n` slices per coin, `gap_ms` apart, each coin at its own clip."""
    days = {}
    for d in range(n_days):
        lo = start_ms + d * records.DAY_MS
        fills = []
        for k, (coin, size) in enumerate(coins):
            t0 = lo + records.HOUR_MS + k * 120_000
            fills += [{"coin": coin, "side": "A", "sz": size, "px": "100.0",
                       "time": t0 + i * gap_ms, "crossed": crossed,
                       "oid": t0 + i * gap_ms, "tid": t0 + i * gap_ms} for i in range(n)]
        entries = [{"order": {"coin": f["coin"], "side": "A", "limitPx": "95.0", "oid": f["oid"],
                              "timestamp": f["time"], "tif": tif, "cloid": cloid,
                              "isTrigger": False, "orderType": "Limit", "reduceOnly": False},
                    "status": "filled"} for f in fills]
        records.fold_fills(days, fills, wallet=wallet, role="studied", start_ms=lo,
                           end_ms=lo + records.DAY_MS, last_fill_ms=None)
        records.fold_orders(days, entries, records.first_prices(fills), wallet=wallet,
                            role="studied", start_ms=lo, end_ms=lo + records.DAY_MS)
    return days


def unmeasure_offsets(days, prefix):
    """The orders were read on those days but no first fill was, so no IOC offset is known."""
    for day, record in days.items():
        if day.startswith(prefix):
            record["habits"]["ioc_offset_seen"] = 0
            record["habits"]["ioc_offset_5pct"] = 0
    return days


def context(his, ref, strangers=200, family_size=10):
    rows = [{"wallet": f"0x{i:040x}", "orders_seen": 500, "style": MAKER, "cadence": None,
             "clip_table": None, "clip_notionals": None, "program_runs": 0, "ioc5": None,
             "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}}
            for i in range(strangers)]
    snap = {"orders_seen": 500, "style": MAKER, "cadence": None,
            "clip_table": None, "clip_notionals": None, "program_runs": 0, "ioc5": None}
    pairs = [(snap, snap)] * (family_size * (family_size - 1) // 2)
    return assemble.panel_context(ref, assemble.self_splits(his, ref), rows, pairs)


def test_his_reference_is_built_by_the_same_code_as_any_wallet():
    ref = assemble.his_reference(history(T, JUNE, 120))
    assert ref["style"] == HIS_STYLE and sum(ref["cadence"]) >= 200
    assert ref["last_day"] == "2026-09-28" and ref["span_days"] == 90
    assert assemble.his_reference({}) == {}


def test_his_reference_widens_until_it_holds_two_hundred_gaps():
    # Eight old days (39 gaps each) and one recent day: the 90-day window holds 39 gaps,
    # so the span doubles to 180 days, which reaches the old days (9 * 39 = 351 gaps).
    his = {**history(T, JUNE, 8), **history(T, JUNE + 119 * records.DAY_MS, 1)}
    ref = assemble.his_reference(his)
    assert ref["last_day"] == "2026-09-28" and ref["span_days"] == 180
    assert sum(ref["cadence"]) == 9 * 39


def test_a_window_is_the_last_n_days_up_to_and_including_the_last_day():
    days = {d: {"day": d} for d in ("2026-06-01", "2026-06-02", "2026-06-03",
                                    "2026-06-04", "2026-06-05")}
    assert [r["day"] for r in assemble.window(days, "2026-06-04", 3)] == [
        "2026-06-02", "2026-06-03", "2026-06-04"]
    assert assemble.window({}, "2026-06-04", 3) == []


def test_his_months_agree_with_the_rest_of_him():
    his = history(T, JUNE, 120)
    splits = assemble.self_splits(his, assemble.his_reference(his))
    assert splits["t1"] == (4, 4) and splits["t2"] == [0.0] * 4


def test_a_month_that_trades_another_way_counts_as_a_disagreement():
    # June rests orders (maker, no slicer); July to September run his program.
    his = {**history(T, JUNE, 30, tif="Alo", crossed=False),
           **history(T, JUNE + 30 * records.DAY_MS, 90)}
    ref = assemble.his_reference(his)
    assert ref["last_day"] == "2026-09-28" and ref["style"] == HIS_STYLE
    splits = assemble.self_splits(his, ref)
    # June differs from the rest of him, and a month with no slicer gaps has no rhythm distance.
    assert splits["t1"] == (3, 4) and splits["t2"] == [0.0] * 3


def test_his_rhythm_months_are_measured_against_his_recent_window():
    # June slices every 2.5 s, July to September every 1.7 s. June is 0.8 s from the rest of
    # him; July is judged against August and September, not against June as well.
    his = {**history(T, JUNE, 30, gap_ms=2_500), **history(T, JUNE + 30 * records.DAY_MS, 90)}
    splits = assemble.self_splits(his, assemble.his_reference(his))
    assert splits["t2"] == [0.8, 0.0, 0.0, 0.0]


def test_a_month_whose_style_cannot_be_decided_is_no_window_not_a_disagreement():
    # June's orders were read but its IOC offsets were not: IOC-dominant with under 50
    # measured offsets has no style (tooling.style), so June is unknown. It must neither
    # count as a window nor as one that disagreed with the rest of him.
    his = unmeasure_offsets(history(T, JUNE, 120), "2026-06")
    splits = assemble.self_splits(his, assemble.his_reference(his))
    assert splits["t1"] == (3, 3)


def test_when_none_of_his_months_can_be_decided_there_is_nothing_to_split():
    # Two undecidable styles are not "the same style": None == None is no agreement.
    his = unmeasure_offsets(history(T, JUNE, 120), "2026-")
    ref = assemble.his_reference(his)
    assert ref["style"] is None
    assert assemble.self_splits(his, ref)["t1"] == (0, 0)


def test_a_wallet_trading_his_way_reads_for_once_the_panels_are_calibrated():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    days = history(W, JUNE + 90 * records.DAY_MS, 20)
    tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, context(his, ref))
    assert tests["T1"]["judgement"]["status"] == "for"
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "for"
    row = assemble.study_row({"wallet": W, "source": "roster_lead", "since_ms": 1}, tests, 2e6, 5)
    assert row["families"]["tooling"]["verdict"] == "for" and row["rank"] > 0
    assert row["families"]["tooling"]["key"]["style"] == "PROGRAM_IOC5"


def test_rhythm_and_clips_read_for_through_the_whole_assembly():
    # Seven months give his own splits the six windows the self basis needs, and three
    # coins give his clip table something to compare.
    his = history(T, JUNE, 210, coins=THREE_COINS, n=20)
    ref = assemble.his_reference(his)
    assert len(assemble.self_splits(his, ref)["t3"]) == 7
    cand = history(W, JUNE + 210 * records.DAY_MS, 20, coins=THREE_COINS, n=20)
    tests = assemble.tooling_tests([cand[d] for d in sorted(cand)], ref, context(his, ref))
    assert [tests[name]["judgement"]["status"] for name in verdict.TOOLING] == ["for"] * 3
    assert tests["T2"]["judgement"]["basis"] == tests["T3"]["judgement"]["basis"] == "self"
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["verdict"] == "for" and got["by"] == ["T1", "T2", "T3"]


def test_the_panel_context_reports_each_panel_against_its_bar():
    his = history(T, JUNE, 120)
    ctx = context(his, assemble.his_reference(his), strangers=200, family_size=10)
    assert ctx["status"] == {"strangers": 200, "family_pairs": 45, "self_windows": 4}
    assert ctx["t1"]["stranger"] == (0, 200)
    assert ctx["t1"]["same_op"] == {"family": (45, 45), "self": (4, 4)}
    assert ctx["t1"]["mismatch"]["client_ids"] == (0, 45)
    assert ctx["t1"]["trait_rates"] == {"client_ids": 1.0, "triggers": 0.0, "maker": 1.0}
    # Strangers who run no program cannot match (inf / -inf); his months are the only yardstick.
    assert ctx["t2"]["strangers"] == [float("inf")] * 200
    assert ctx["t3"]["strangers"] == [float("-inf")] * 200
    assert ctx["t2"]["same_op"] == {"family": [], "self": [0.0] * 4}


def test_a_study_row_and_its_dossier_carry_what_the_page_draws():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    days = history(W, JUNE + 90 * records.DAY_MS, 20)
    tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, context(his, ref))
    member = {"wallet": W, "source": "roster_lead", "since_ms": 1}
    row = assemble.study_row(member, tests, 2e6, 5)
    assert set(row) == {"wallet", "source", "studied_since_ms", "account_value", "coverage_days",
                        "orders", "last_read_ms", "families", "rank"}
    assert (row["studied_since_ms"], row["last_read_ms"], row["account_value"]) == (1, 5, 2e6)
    assert row["coverage_days"] == 20 and row["orders"] == 800
    tooling = row["families"]["tooling"]
    assert set(tooling) == {"verdict", "lr", "by", "basis", "key"}
    assert tooling["by"] == ["T1"] and tooling["basis"] == "family" and tooling["lr"] > 25
    assert row["rank"] == verdict.rank(row["families"])
    doc = assemble.dossier(member, tests, ref)
    assert doc["schema"] == assemble.SCHEMA == "study/1"
    assert doc["wallet"] == W and doc["source"] == "roster_lead"
    assert set(doc["tests"]) == {"T1", "T2", "T3"} and set(doc["series"]) == {
        "cadence", "his_cadence", "shares", "his_shares"}
    assert doc["series"]["his_cadence"] == ref["cadence"] and "computed_at" not in doc
    assert doc["series"]["shares"]["ioc"] == 1.0 == doc["series"]["his_shares"]["ioc"]


def test_a_maker_bot_with_client_ids_reads_against():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    bot = history(BOT, JUNE + 90 * records.DAY_MS, 20, tif="Alo", cloid="0x01", crossed=False)
    tests = assemble.tooling_tests([bot[d] for d in sorted(bot)], ref, context(his, ref))
    assert tests["T1"]["against"]["status"] == "against"
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "against"


def test_small_panels_read_uncalibrated_never_for_or_against():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    bot = history(BOT, JUNE + 90 * records.DAY_MS, 20, tif="Alo", cloid="0x01", crossed=False)
    tests = assemble.tooling_tests([bot[d] for d in sorted(bot)], ref,
                                   context(his, ref, strangers=10, family_size=3))
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "uncalibrated"


def test_against_waits_for_two_hundred_strangers_even_with_enough_family_pairs():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    bot = history(BOT, JUNE + 90 * records.DAY_MS, 20, tif="Alo", cloid="0x01", crossed=False)
    days = [bot[d] for d in sorted(bot)]
    assert assemble.tooling_tests(days, ref, context(his, ref, strangers=199))[
        "T1"]["against"]["status"] == "uncalibrated"
    assert assemble.tooling_tests(days, ref, context(his, ref, strangers=200))[
        "T1"]["against"]["status"] == "against"


def test_a_wallet_never_read_is_insufficient():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    tests = assemble.tooling_tests([], ref, context(his, ref))
    assert verdict.family_verdict(tests, verdict.TOOLING)["verdict"] == "insufficient"


def test_the_latest_document_orders_by_rank_and_states_the_bars():
    rows = [{"wallet": "0x1", "rank": 0.0, "account_value": 5.0},
            {"wallet": "0x2", "rank": 1.5, "account_value": 1.0}]
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, {}, {"strangers": 3}, rows,
                              {"read": ["0x1"], "unreadable": [], "stopped": False})
    assert [r["wallet"] for r in doc["wallets"]] == ["0x2", "0x1"]
    assert doc["panels"]["bars"] == {"strangers": 200, "family_pairs": 40, "self_windows": 6}
    assert doc["studied"] == 2 and doc["read"] == 1


def test_equal_ranks_order_by_size_then_address_and_the_document_reports_the_read():
    rows = [{"wallet": "0xb", "rank": 1.0, "account_value": 5.0},
            {"wallet": "0xa", "rank": 1.0, "account_value": 5.0},
            {"wallet": "0xc", "rank": 1.0, "account_value": None},
            {"wallet": "0xd", "rank": 1.0, "account_value": 9.0}]
    ref = {"style": HIS_STYLE, "last_day": "2026-09-28", "cadence": [1, 2, 3]}
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, ref, {"strangers": 3}, rows,
                              {"read": [], "unreadable": ["0x9"], "stopped": True,
                               "budget": {"used": 1}})
    assert [r["wallet"] for r in doc["wallets"]] == ["0xd", "0xa", "0xb", "0xc"]
    assert doc["wallets"][3]["account_value"] is None  # unknown is not zero
    assert doc["reference"] == {"style": HIS_STYLE, "last_day": "2026-09-28", "cadence_gaps": 6}
    assert doc["unreadable"] == ["0x9"] and doc["stopped"] is True and doc["budget"] == {"used": 1}
    assert doc["schema"] == "study/1" and doc["target"] == T and doc["panels"]["strangers"] == 3
