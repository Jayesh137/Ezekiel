"""Assembly: his reference, his months, the panels, and each wallet's tooling verdict."""

import pytest

from src.study import assemble, panels, records, tooling, verdict

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


SNAP = {"orders_seen": 500, "style": MAKER, "cadence": None, "clip_table": None,
        "clip_notionals": None, "program_runs": 0, "ioc5": None}


def stranger_rows(n, undecidable=0):
    """`n` measurable strangers, the first `undecidable` of them with no decidable style."""
    return [{"wallet": f"0x{i:040x}", "orders_seen": 500, "style": None if i < undecidable else MAKER,
             "cadence": None, "clip_table": None, "clip_notionals": None, "program_runs": 0,
             "ioc5": None, "shares": {"client_ids": 1.0, "maker": 1.0, "triggers": 0.0}}
            for i in range(n)]


def family_pairs(size, rhythm=0, clips=0):
    """One family of `size` members paired the way the study pairs it (a star: its first
    member against each other, `size - 1` pairs). The first member and the next `rhythm`
    members run a slicer; the first and the next `clips` carry a clip table."""
    members = [f"0x{i:040x}" for i in range(size)]
    gaps = [0] * records.CADENCE_BINS
    gaps[17] = tooling.MIN_GAPS
    snaps = {m: {**SNAP, "cadence": gaps if i <= rhythm else None,
                 "clip_table": {"BTC": 0.1, "ETH": 1.0} if i <= clips else None}
             for i, m in enumerate(members)}
    return panels.member_pairs({"m": members}, snaps)


def context(his, ref, strangers=200, family_size=41, undecidable=0, **family):
    # 41 members are 40 pairs: exactly the family bar.
    return assemble.panel_context(ref, assemble.self_splits(his, ref),
                                  stranger_rows(strangers, undecidable),
                                  family_pairs(family_size, **family))


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


def clips(btc):
    return (("BTC", btc), ("ETH", "1.0"), ("SOL", "10.0"))


def differing_months():
    """Three consecutive months of his that differ: June slices every 1.0 s with a 2.0 BTC
    clip, July every 1.7 s with a 0.2 BTC clip, August as July with a client id on every
    order. A day is three runs of 20 slices: 60 orders and 57 gaps."""
    return {**history(T, JUNE, 30, coins=clips("2.0"), gap_ms=1_000, n=20),
            **history(T, JUNE + 30 * records.DAY_MS, 31, coins=clips("0.2"), n=20),
            **history(T, JUNE + 61 * records.DAY_MS, 31, coins=clips("0.2"), n=20, cloid="0x01")}


@pytest.fixture(scope="module")
def months():
    his = differing_months()
    ref = assemble.his_reference(his)
    return his, ref, assemble.self_splits(his, ref)


def current_days():
    """Twenty covered days of a wallet slicing his current way with a client id on every
    order, and a 21st day of which only five hours were read."""
    days = history(W, JUNE + 92 * records.DAY_MS, 20, coins=clips("0.2"), n=20, cloid="0x01")
    lo = JUNE + 112 * records.DAY_MS
    records.fold_fills(days, [], wallet=W, role="studied", start_ms=lo,
                       end_ms=lo + 5 * records.HOUR_MS, last_fill_ms=None)
    return [days[d] for d in sorted(days)]


def test_his_reference_takes_each_field_from_its_own_window(months):
    his, ref, _ = months
    assert len(his) == 92 and ref["last_day"] == "2026-08-31" and ref["span_days"] == 90
    # The recent window is the last 90 days, so it starts on June 3: his rhythm (28 June days
    # at 1.0 s, 62 days at 1.7 s) and his recent profile come from it...
    assert (ref["cadence"][10], ref["cadence"][17]) == (28 * 57, 62 * 57)
    assert ref["recent_profile"]["orders_seen"] == 90 * 60
    # ...his whole-history profile and clip signature from all 92 days. BTC is no clip: its
    # modal size, 0.2, has 1,240 of 1,840 orders and a clip needs 80%.
    assert ref["full_profile"]["orders_seen"] == 92 * 60
    assert ref["signature"]["clip_table"] == {
        "ETH": {"size": 1.0, "share": 1.0, "count": 92 * 20},
        "SOL": {"size": 10.0, "share": 1.0, "count": 92 * 20}}


def test_each_of_his_months_is_left_out_of_the_style_and_rhythm_it_is_judged_against(months):
    _, _, splits = months
    # T1: June and July carry no client id and August does. Left in its own rest, August
    # would agree with it; left out, none of the three does.
    assert splits["t1"] == (0, 3)
    # T2: June, at 1.0 s, is 0.7 s from the rest (July and August, at 1.7 s). July and August
    # are each 0.3322 s from theirs, which holds June's 28 recent days at 1.0 s.
    assert splits["t2"] == [0.7, 0.3322, 0.3322]


def test_his_clip_strengths_compare_each_month_with_the_other_months_only(months):
    _, _, splits = months
    # June clips BTC at 2.0 and the others at 0.2. Of the three coins in both signatures two
    # match (0.6667) and the notional ranks agree at 0.5; the 5% offset habit adds 0.05 and
    # June's 1.0 s gap is no program, so there is no cadence bonus: 0.7167. For July and
    # August the other months hold both BTC clips, so BTC is no clip and ETH and SOL match.
    assert splits["t3"] == [0.7167, 1.0, 1.0]


def test_his_months_are_split_inside_the_window_the_reference_widened_to():
    # Ten days of May at 1.0 s, ten of June at 1.7 s, the last four of September at 1.7 s.
    # The last 90 days hold 156 gaps, under the 200 a rhythm needs, so the reference widens
    # to 180 days and his splits must use that window: May is 0.7 s from the rest of it,
    # June 0.5 s. (Cut to 90 days, no month has a rest to be compared with.)
    his = {**history(T, JUNE - 31 * records.DAY_MS, 10, gap_ms=1_000),
           **history(T, JUNE, 10), **history(T, JUNE + 116 * records.DAY_MS, 4)}
    ref = assemble.his_reference(his)
    assert ref["last_day"] == "2026-09-28" and ref["span_days"] == 180
    assert sum(ref["cadence"]) == 936
    assert assemble.self_splits(his, ref)["t2"] == [0.7, 0.5]


def test_key_numbers_report_each_key_from_its_own_test(months):
    his, ref, _ = months
    tests = assemble.tooling_tests(current_days(), ref, context(his, ref))
    # Rhythm: 0.7 s from the 1.0 s gaps that are 31% of his recent ones. Clips: ETH and SOL match.
    assert assemble.key_numbers(tests) == {
        "style": "PROGRAM_IOC5", "client_ids": 1.0, "maker": 0.0,
        "rhythm_s": 0.2178, "clip_strength": 1.0}


def test_a_row_counts_covered_days_not_days_read(months):
    his, ref, _ = months
    tests = assemble.tooling_tests(current_days(), ref, context(his, ref))
    assert tests["summary"]["days"] == 21 and tests["summary"]["covered_days"] == 20
    row = assemble.study_row({"wallet": W, "source": "roster_lead", "since_ms": 1}, tests, 2e6, 5)
    assert row["coverage_days"] == 20 and row["orders"] == 1200


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
    ctx = context(his, ref)
    assert len(assemble.self_splits(his, ref)["t3"]) == 7
    # The header shows his seven windows for each of the three tests.
    assert [ctx["status"]["by_test"][name]["self_windows"] for name in ("T1", "T2", "T3")] == [7] * 3
    cand = history(W, JUNE + 210 * records.DAY_MS, 20, coins=THREE_COINS, n=20)
    tests = assemble.tooling_tests([cand[d] for d in sorted(cand)], ref, ctx)
    assert [tests[name]["judgement"]["status"] for name in verdict.TOOLING] == ["for"] * 3
    assert tests["T2"]["judgement"]["basis"] == tests["T3"]["judgement"]["basis"] == "self"
    got = verdict.family_verdict(tests, verdict.TOOLING)
    assert got["verdict"] == "for" and got["by"] == ["T1", "T2", "T3"]


def test_the_panel_context_reports_each_panel_against_its_bar():
    his = history(T, JUNE, 120)
    ctx = context(his, assemble.his_reference(his), strangers=200)
    assert ctx["status"]["family_pairs"] == 40  # 41 members, paired as a star
    # T3 has no self windows (one coin, so no clip table).
    assert ctx["status"] == {
        "strangers": 200, "measurable_strangers": 200, "family_pairs": 40, "self_windows": 4,
        "by_test": {"T1": {"strangers": 200, "family_pairs": 40, "self_windows": 4},
                    "T2": {"strangers": 200, "family_pairs": 0, "self_windows": 4},
                    "T3": {"strangers": 200, "family_pairs": 0, "self_windows": 0}}}
    assert ctx["t1"]["stranger"] == (0, 200)
    assert ctx["t1"]["same_op"] == {"family": (40, 40), "self": (4, 4)}
    assert ctx["t1"]["mismatch"]["client_ids"] == (0, 40)
    assert ctx["t1"]["trait_rates"] == {"client_ids": 1.0, "triggers": 0.0, "maker": 1.0}
    # Strangers who run no program cannot match (inf / -inf); his months are the only yardstick.
    assert ctx["t2"]["strangers"] == [float("inf")] * 200
    assert ctx["t3"]["strangers"] == [float("-inf")] * 200
    assert ctx["t2"]["same_op"] == {"family": [], "self": [0.0] * 4}


def test_the_header_counts_the_strangers_the_judgement_used():
    # 210 measurable strangers, 25 of whom have no decidable style. T1 and `against` judge on
    # the 185 whose style is known, so that is what the header shows (the bar is 200), with
    # the 210 beside it: "uncalibrated" must be visibly a property of the panel.
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    ctx = context(his, ref, strangers=210, undecidable=25)
    status = ctx["status"]
    assert status["strangers"] == 185 and status["measurable_strangers"] == 210
    assert status["by_test"]["T1"]["strangers"] == 185
    # T2 and T3 need no style: all 210 count for them.
    assert status["by_test"]["T2"]["strangers"] == status["by_test"]["T3"]["strangers"] == 210
    days = history(W, JUNE + 90 * records.DAY_MS, 20)
    tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref, ctx)
    assert tests["T1"]["judgement"]["status"] == "uncalibrated"
    assert tests["T1"]["judgement"]["strangers"] == status["strangers"]


def test_each_test_reports_the_panels_it_was_judged_on():
    his = history(T, JUNE, 120)
    ref = assemble.his_reference(his)
    # No pair has a slicing rhythm or a clip table on both sides: T1 keeps all 40 pairs, T2
    # and T3 have none.
    plain = context(his, ref)["status"]
    assert plain["family_pairs"] == 40 and plain["by_test"]["T1"]["family_pairs"] == 40
    assert plain["by_test"]["T2"]["family_pairs"] == 0 and plain["by_test"]["T3"]["family_pairs"] == 0
    # Ten pairs where both members slice, five where both hold a clip table.
    mixed = context(his, ref, rhythm=10, clips=5)["status"]
    assert mixed["family_pairs"] == 40 and mixed["by_test"]["T1"]["family_pairs"] == 40
    assert mixed["by_test"]["T2"]["family_pairs"] == 10
    assert mixed["by_test"]["T3"]["family_pairs"] == 5
    # Strangers who cannot produce a rhythm or a clip are non-matches and still count.
    assert plain["by_test"]["T2"]["strangers"] == plain["by_test"]["T3"]["strangers"] == 200


def test_each_test_counts_the_months_of_his_it_could_use():
    # June rests orders: a style window for T1, but it has no slicer gaps for T2 and no clip
    # table for T3 (one coin). So his four months are 4 windows, 3 and 0.
    his = {**history(T, JUNE, 30, tif="Alo", crossed=False),
           **history(T, JUNE + 30 * records.DAY_MS, 90)}
    status = context(his, assemble.his_reference(his))["status"]
    assert status["self_windows"] == 4
    assert [status["by_test"][name]["self_windows"] for name in ("T1", "T2", "T3")] == [4, 3, 0]


def test_the_latest_document_carries_the_whole_panel_status_beside_the_bars():
    his = history(T, JUNE, 120)
    ctx = context(his, assemble.his_reference(his), strangers=210, undecidable=25, rhythm=10)
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, {}, ctx["status"], [], {})
    assert doc["panels"]["strangers"] == 185 and doc["panels"]["measurable_strangers"] == 210
    assert doc["panels"]["family_pairs"] == 40 and doc["panels"]["self_windows"] == 4
    assert doc["panels"]["by_test"] == ctx["status"]["by_test"]
    assert doc["panels"]["by_test"]["T2"]["family_pairs"] == 10
    assert doc["panels"]["bars"] == {"strangers": 200, "family_pairs": 40, "self_windows": 6}


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
    family = row["families"]["tooling"]
    assert set(family) == {"verdict", "lr", "by", "basis", "key"}
    assert family["by"] == ["T1"] and family["basis"] == "family" and family["lr"] > 25
    assert row["rank"] == verdict.rank(row["families"])
    doc = assemble.dossier(member, tests, ref)
    assert doc["schema"] == assemble.SCHEMA == "study/1"
    assert doc["wallet"] == W and doc["source"] == "roster_lead"
    # Only the candidate's own series: his live once, in the latest document's reference.
    assert set(doc["tests"]) == {"T1", "T2", "T3"} and set(doc["series"]) == {"cadence", "shares"}
    assert doc["series"]["cadence"] == tests["summary"]["cadence"] and "computed_at" not in doc
    assert doc["series"]["shares"]["ioc"] == 1.0


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
    assert doc["reference"] == {"style": HIS_STYLE, "last_day": "2026-09-28", "cadence_gaps": 6,
                                "cadence": [1, 2, 3], "shares": None}
    assert doc["unreadable"] == ["0x9"] and doc["stopped"] is True and doc["budget"] == {"used": 1}
    assert doc["schema"] == "study/1" and doc["target"] == T and doc["panels"]["strangers"] == 3


def test_his_series_live_once_in_the_latest_document():
    # His recent window (July 1 to September 28) holds 31 days of resting orders and 59 of
    # IOC slices; June, which only his whole history sees, is all IOC (0.7417).
    his = {**history(T, JUNE, 30),
           **history(T, JUNE + 30 * records.DAY_MS, 31, tif="Alo", crossed=False),
           **history(T, JUNE + 61 * records.DAY_MS, 59)}
    ref = assemble.his_reference(his)
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, ref, {}, [], {})
    assert doc["reference"]["cadence"] == ref["cadence"] and sum(ref["cadence"]) == 59 * 39
    shares = doc["reference"]["shares"]
    assert set(shares) == set(tooling.SHARE_KEYS)
    assert shares["ioc"] == round(59 / 90, 4) == 0.6556 and shares["alo"] == round(31 / 90, 4)


def test_his_shares_round_to_four_places_keep_unknown_as_unknown_and_vanish_with_no_profile():
    shares = dict.fromkeys(tooling.SHARE_KEYS, 0.0) | {"ioc": 0.123456, "taker": None}
    doc = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, {"recent_profile": shares}, {}, [], {})
    assert doc["reference"]["shares"]["ioc"] == 0.1235 and doc["reference"]["shares"]["taker"] is None
    bare = assemble.latest_doc("2026-10-06T00:00:00+00:00", T, {}, {}, [], {})
    assert bare["reference"]["shares"] is None and bare["reference"]["cadence"] is None


def test_two_dossiers_for_one_wallet_do_not_move_with_his_reference():
    # His records are rebuilt every run and his series move with them. A candidate's dossier
    # must not, or every dossier is rewritten each run (spec 10: only when its content changes).
    short, long = history(T, JUNE, 40), history(T, JUNE, 120)
    ref_a, ref_b = assemble.his_reference(short), assemble.his_reference(long)
    assert ref_a["cadence"] != ref_b["cadence"]
    days = history(W, JUNE + 120 * records.DAY_MS, 20)
    tests = assemble.tooling_tests([days[d] for d in sorted(days)], ref_b, context(long, ref_b))
    member = {"wallet": W, "source": "roster_lead", "since_ms": 1}
    assert assemble.dossier(member, tests, ref_a) == assemble.dossier(member, tests, ref_b)


def test_his_style_is_not_published_from_a_sparse_recent_window():
    # Under 100 orders in the recent window there is no style to publish (tooling.MIN_ORDERS).
    assert assemble.his_reference(history(T, JUNE, 1, n=50))["style"] is None
    assert assemble.his_reference(history(T, JUNE, 1, n=99))["style"] is None
    assert assemble.his_reference(history(T, JUNE, 1, n=100))["style"] == HIS_STYLE
