"""The script's timing constant, as evidence beside the clip table.

Measured 2026-11-13: his clip sizes change every campaign, so against his own
later trading the clip table clears the gate in 6 of 15 thirty-day windows; the
pause between his orders held a 25th percentile of 1.58-1.68 s for eight months.
Among 106 measurable wallets of his client class, 2 share the band. Evidence,
never a vote (the ceiling on 2 of 106 is 6.6%).
"""

from src import execution_program as ep

DAY = 86_400_000


def _orders(gaps_s, coin="ZEC", side="B", start=1_000_000_000_000, taker=True):
    out, t = [], start
    for gap in [0, *gaps_s]:
        t += int(gap * 1000)
        out.append({"t": t, "coin": coin, "side": side, "base_size": 1.0, "taker": taker})
    return out


def test_cadence_reads_the_quartile_and_median_of_the_loop_pause():
    c = ep.cadence(_orders([1.6] * 30 + [1.7] * 30 + [2.4] * 30))
    assert c == {"gaps": 90, "p25": 1.6, "p50": 1.7}


def test_too_few_pauses_is_unmeasured_not_zero():
    assert ep.cadence(_orders([1.6] * 10)) is None
    assert ep.cadence([]) is None


def test_a_pause_longer_than_the_loop_is_a_new_decision_and_not_counted():
    c = ep.cadence(_orders([1.6] * 60 + [45.0, 300.0] + [1.6] * 10))
    assert c["gaps"] == 70


def test_pauses_are_measured_within_one_coin_and_side_never_across():
    """Two interleaved programs each at 2 s must not read as a 1 s loop."""
    a = _orders([2.0] * 60, coin="ZEC")
    b = _orders([2.0] * 60, coin="NEAR", start=1_000_000_001_000)
    assert ep.cadence(a + b)["p50"] == 2.0


def test_maker_orders_carry_no_cadence():
    assert ep.cadence(_orders([1.6] * 80, taker=False)) is None


def test_only_the_newest_window_is_read_so_an_old_faster_loop_does_not_blur_it():
    old = _orders([0.4] * 200, start=1_000_000_000_000)
    new = _orders([1.6] * 60, start=1_000_000_000_000 + 200 * DAY)
    assert ep.cadence(old + new)["p25"] == 1.6


def test_match_needs_both_quantiles_inside_his_own_spread():
    him = {"cadence": {"gaps": 600, "p25": 1.58, "p50": 1.65}}
    assert ep.cadence_match(him, {"cadence": {"gaps": 90, "p25": 1.64, "p50": 2.0}}) is True
    assert ep.cadence_match(him, {"cadence": {"gaps": 90, "p25": 1.25, "p50": 1.7}}) is False
    assert ep.cadence_match(him, {"cadence": {"gaps": 90, "p25": 1.6, "p50": 2.8}}) is False


def test_an_unreadable_cadence_is_unknown_never_a_mismatch():
    him = {"cadence": {"gaps": 600, "p25": 1.58, "p50": 1.65}}
    assert ep.cadence_match(him, {"cadence": None}) is None
    assert ep.cadence_match({"cadence": None}, him) is None
    assert ep.cadence_match(him, {}) is None


def test_a_wallet_paced_like_him_on_none_of_his_coins_stays_in_the_report():
    target = {"clip_table": {"ZEC": {"size": 1.0}}, "cadence": {"gaps": 600, "p25": 1.58, "p50": 1.65}}
    paced = {"wallet": "0xa", "signature": {"clip_table": {}, "cadence": {"gaps": 80, "p25": 1.6, "p50": 1.7}}}
    other = {"wallet": "0xb", "signature": {"clip_table": {}, "cadence": {"gaps": 80, "p25": 0.4, "p50": 0.5}}}
    blank = {"wallet": "0xc", "signature": {"clip_table": {}}}
    rows = ep.rank_matches(target, [paced, other, blank])
    assert [(r["wallet"], r["status"]) for r in rows] == [("0xa", "cadence_only")]


def test_cadence_alone_never_votes_however_close():
    target = {"clip_table": {"ZEC": {"size": 1.0}}, "cadence": {"gaps": 600, "p25": 1.58, "p50": 1.65}}
    row = ep.rank_matches(target, [{"wallet": "0xa", "signature": {
        "clip_table": {}, "cadence": {"gaps": 80, "p25": 1.58, "p50": 1.65}}}])[0]
    census = {"population": 279, "ratio_p99": 0.5, "rho_population": 119, "rho_p99": 1.0,
              "attempted": 1289, "comparable_upper": 0.02, "min_clips": 3}
    assert ep.is_discriminating(row, census) is False


def test_the_signature_carries_the_cadence():
    fills = [{"time": 1_000_000_000_000 + i * 1600, "sz": "1", "px": "10", "side": "B",
              "coin": "ZEC", "oid": i, "tid": i, "crossed": True} for i in range(80)]
    assert ep.signature(fills)["cadence"] == {"gaps": 79, "p25": 1.6, "p50": 1.6}


# --- the base-rate census (scripts/census_cadence.py) -------------------------

def test_census_counts_only_measurable_wallets_and_names_those_in_band():
    from scripts import census_cadence as cc
    him = {"cadence": {"gaps": 600, "p25": 1.6, "p50": 1.7}}
    readings = {"0xa": {"gaps": 80, "p25": 1.62, "p50": 1.8},
                "0xb": {"gaps": 80, "p25": 0.4, "p50": 0.5},
                "0xc": None}
    out = cc.summarise(him, readings, ["0xd"])
    assert (out["read"], out["measured"], out["unreadable"], out["in_band"]) == (3, 2, 1, 1)
    assert out["wallets_in_band"][0]["wallet"] == "0xa"
    assert out["votes"] is False            # two wallets measure nothing


def test_census_never_votes_above_the_false_match_ceiling():
    from scripts import census_cadence as cc
    him = {"cadence": {"gaps": 600, "p25": 1.6, "p50": 1.7}}
    readings = {f"0x{i:03d}": {"gaps": 80, "p25": 0.4, "p50": 0.5} for i in range(104)}
    readings["0xin1"] = readings["0xin2"] = {"gaps": 80, "p25": 1.6, "p50": 1.7}
    out = cc.summarise(him, readings, [])
    assert out["in_band"] == 2 and out["measured"] == 106
    assert out["upper"] > ep.MAX_FALSE_MATCH_RATE and out["votes"] is False


def test_class_wallets_leaves_out_his_own_and_everyone_outside_the_class():
    from scripts import census_cadence as cc
    profiles = {"0xa": {"his": True}, "0xb": {"his": False}, "0xme": {"his": True}, "0xc": "junk"}
    assert cc.class_wallets(profiles, {"0xme"}) == ["0xa"]
