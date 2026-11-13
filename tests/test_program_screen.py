"""The venue-wide screen for his script, day by day (`src/program_screen.py`).

Measured 2026-11-13 on two days he traded heavily: of 67,044 wallets on his
markets exactly one showed BOTH his sizes and his cadence — his own. These pin
the two properties that result rests on: the conjunction is the hit, and a day
is judged only against what he looked like before it.
"""

from scripts import screen_program_history as sph
from src import program_screen as ps

DAY = ps.DAY_MS
T0 = ps.day_start_ms("2026-08-01")


def _own(coin, size, start, n=40, gap_s=1.6, side="B"):
    return [{"t": start + int(i * gap_s * 1000), "coin": coin, "side": side,
             "base_size": size, "taker": True, "first_px": 10.0} for i in range(n)]


def _tape(wallet, coin, size, start, n=40, gap_s=1.6, side="B"):
    return [{"wallet": wallet, "coin": coin, "side": side, "base_size": size,
             "t": start + int(i * gap_s * 1000), "first_px": 10.0, "taker": True}
            for i in range(n)]


def _history():
    """Him in July: three coins, a 1.6 s loop."""
    july = ps.day_start_ms("2026-07-10")
    return (_own("NEAR", 250.0, july) + _own("ADA", 5000.0, july + 3_600_000)
            + _own("MON", 100000.0, july + 7_200_000))


def _day(*rows):
    state = ps.empty_state()
    for chunk in rows:
        ps.fold(state, chunk)
    return state


def test_reference_holds_only_what_he_did_before_the_day():
    orders = _history() + _own("SOL", 100.0, T0 + 2 * DAY)
    ref = ps.reference(orders, T0)
    assert ref["sizes"] == {"NEAR": [250.0], "ADA": [5000.0], "MON": [100000.0]}
    assert ref["cadence"]["p25"] == 1.6
    assert "SOL" in ps.reference(orders, T0 + 3 * DAY)["sizes"]


def test_reference_keeps_every_size_he_used_on_a_coin_across_months():
    orders = _own("HYPE", 500.0, ps.day_start_ms("2026-02-10")) + _own("HYPE", 40.0, ps.day_start_ms("2026-03-10"))
    assert ps.reference(orders, T0)["sizes"]["HYPE"] == [40.0, 500.0]


def test_btc_and_zec_are_not_read():
    ref = {"sizes": {"BTC": [0.1], "ZEC": [1.0], "NEAR": [250.0]}}
    assert ps.coins_to_read(ref) == ["NEAR"]


def test_a_wallet_with_his_sizes_AND_his_cadence_is_the_hit():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xa", "NEAR", 250.0, T0), _tape("0xa", "ADA", 5000.0, T0 + 3_600_000),
                 _tape("0xa", "MON", 100000.0, T0 + 7_200_000))
    out = ps.judge_day(state, ref, target="0xme")
    assert [h["wallet"] for h in out["hits"]] == ["0xa"]
    assert out["hits"][0]["clips_matched"] == 3 and out["hits"][0]["cadence_match"] is True


def test_his_cadence_alone_is_not_a_hit():
    """32 of 1,632 judgeable wallets paced like him on the control days."""
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xa", "NEAR", 7.0, T0), _tape("0xa", "ADA", 11.0, T0 + 3_600_000),
                 _tape("0xa", "MON", 13.0, T0 + 7_200_000))
    out = ps.judge_day(state, ref, target="0xme")
    assert out["hits"] == [] and out["cadence_hits"] == 1 and out["clip_hits"] == 0


def test_his_sizes_at_another_pace_are_not_a_hit():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xa", "NEAR", 250.0, T0, gap_s=0.4), _tape("0xa", "ADA", 5000.0, T0 + 3_600_000, gap_s=0.4),
                 _tape("0xa", "MON", 100000.0, T0 + 7_200_000, gap_s=0.4))
    out = ps.judge_day(state, ref, target="0xme")
    assert out["hits"] == [] and out["clip_hits"] == 1 and out["cadence_hits"] == 0


def test_two_of_his_sizes_with_his_cadence_is_near_and_kept_but_not_a_hit():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xa", "NEAR", 250.0, T0), _tape("0xa", "ADA", 5000.0, T0 + 3_600_000))
    out = ps.judge_day(state, ref, target="0xme")
    assert out["hits"] == [] and [n["wallet"] for n in out["near"]] == ["0xa"]


def test_his_own_wallet_is_the_control_and_never_a_hit():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xme", "NEAR", 250.0, T0), _tape("0xme", "ADA", 5000.0, T0 + 3_600_000),
                 _tape("0xme", "MON", 100000.0, T0 + 7_200_000))
    out = ps.judge_day(state, ref, target="0xME")
    assert out["hits"] == [] and out["target_found"] is True and out["judged"] == 0


def test_a_day_he_barely_traded_is_unmeasured_for_the_control_not_a_miss():
    ref = ps.reference(_history(), T0)
    out = ps.judge_day(_day(_tape("0xme", "NEAR", 250.0, T0, n=10)), ref, target="0xme")
    assert out["target_seen"] is True and out["target_found"] is None
    assert ps.judge_day(_day(), ref, target="0xme")["target_found"] is None


def test_a_day_he_traded_coins_his_history_never_held_is_a_real_miss():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xme", "SOL", 100.0, T0), _tape("0xme", "XRP", 1000.0, T0 + 3_600_000),
                 _tape("0xme", "ETH", 2.0, T0 + 7_200_000))
    assert ps.judge_day(state, ref, target="0xme")["target_found"] is False


def test_known_wallets_of_his_are_excluded_from_hits():
    ref = ps.reference(_history(), T0)
    state = _day(_tape("0xk", "NEAR", 250.0, T0), _tape("0xk", "ADA", 5000.0, T0 + 3_600_000),
                 _tape("0xk", "MON", 100000.0, T0 + 7_200_000))
    assert ps.judge_day(state, ref, target="0xme", exclude={"0xK"})["hits"] == []


def test_pauses_never_cross_coins_or_sides():
    state = _day(_tape("0xa", "NEAR", 1.0, T0, gap_s=2.0), _tape("0xa", "ADA", 1.0, T0 + 1000, gap_s=2.0))
    assert ps.wallet_cadence(state["gaps"]["0xa"])["p50"] == 2.0


def test_a_modal_size_below_the_share_bar_is_not_a_clip():
    state = _day(_tape("0xa", "NEAR", 250.0, T0, n=10), _tape("0xa", "NEAR", 7.0, T0 + DAY // 2, n=10))
    assert ps.wallet_table(state["sizes"]["0xa"]) == {}


def test_plan_reads_silent_days_first_newest_first_then_active_days():
    his = {"2026-08-02", "2026-08-04"}
    got = ps.plan(his, "2026-08-01", "2026-08-05", done={"2026-08-05"})
    assert got == [("2026-08-03", True), ("2026-08-01", True),
                   ("2026-08-04", False), ("2026-08-02", False)]


def test_summary_measures_recall_only_on_days_he_was_measurable():
    days = {"a": {"silent": True, "judged": 100, "cadence_hits": 2, "clip_hits": 0, "target_found": None},
            "b": {"silent": False, "judged": 200, "cadence_hits": 3, "clip_hits": 1, "target_found": True},
            "c": {"silent": False, "judged": 50, "cadence_hits": 0, "clip_hits": 0, "target_found": False},
            "d": {"silent": False, "judged": 10, "cadence_hits": 0, "clip_hits": 0, "target_found": None},
            "e": {"silent": True, "error": "unread"}}
    out = ps.summarise(days, {"0xa": {"silent_days": ["a"]}, "0xb": {"silent_days": []}})
    assert (out["days_read"], out["silent_days_read"], out["active_days_read"]) == (4, 1, 3)
    assert (out["target_found_days"], out["target_measurable_days"]) == (1, 2)
    assert out["wallet_days_judged"] == 360 and out["hit_wallets_on_silent_days"] == 1


# --- the script's memory and its strictness ----------------------------------

def _hit(wallet="0xa", clips=3, notional=1000):
    return {"wallet": wallet, "cadence": {"gaps": 60, "p25": 1.6, "p50": 1.7}, "cadence_match": True,
            "clips_compared": clips, "clips_matched": clips, "clips_matched_coins": ["NEAR", "ADA", "MON"],
            "notional": notional, "hit": True}


def test_a_wallet_is_fresh_once_and_again_when_first_seen_on_a_silent_day():
    doc = {"days": {}, "hits": {}}
    assert len(sph.record_hits(doc, "2026-08-02", False, [_hit()])) == 1
    assert sph.record_hits(doc, "2026-08-04", False, [_hit()]) == []
    fresh = sph.record_hits(doc, "2026-08-05", True, [_hit()])
    assert len(fresh) == 1 and fresh[0]["silent"] is True
    assert sph.record_hits(doc, "2026-08-06", True, [_hit()]) == []
    assert doc["hits"]["0xa"]["days"] == ["2026-08-02", "2026-08-04", "2026-08-05", "2026-08-06"]
    assert doc["hits"]["0xa"]["silent_days"] == ["2026-08-05", "2026-08-06"]


def test_the_best_reading_of_a_hit_wallet_is_kept():
    doc = {"days": {}, "hits": {}}
    sph.record_hits(doc, "2026-08-02", True, [_hit(clips=3)])
    sph.record_hits(doc, "2026-08-03", True, [_hit(clips=5)])
    assert doc["hits"]["0xa"]["best"]["clips_matched"] == 5 and doc["hits"]["0xa"]["best"]["day"] == "2026-08-03"


class _Index:
    def block_at(self, ms, post=None):
        return ms // 1000


def test_a_day_is_folded_chunk_by_chunk_and_resumes_inside_a_short_read():
    day = "2026-08-02"
    lo = ps.day_start_ms(day)
    calls = []

    def stream(a, b, coins=None, post=None):
        calls.append((a, b))
        fill = {"user": "0xa", "coin": "NEAR", "px": 10.0, "sz": 250.0, "time": lo + len(calls),
                "side": "B", "hash": f"h{len(calls)}", "crossed": True}
        if len(calls) == 1:                       # short read: resume after last_block
            return {"fills": [fill], "complete": False, "last_block": a + 10, "bytes": 5, "requests": 1}
        return {"fills": [fill], "complete": True, "last_block": b, "bytes": 5, "requests": 1}

    got = sph.read_day(day, ["NEAR"], _Index(), deadline=float("inf"), stream=stream)
    assert calls[1][0] == calls[0][0] + 11
    assert sum(got["state"]["sizes"]["0xa"]["NEAR"].values()) == len(calls)
    assert got["bytes"] == 5 * len(calls)


def test_a_day_that_cannot_be_read_whole_raises_and_is_never_a_quiet_day():
    import pytest

    from src import fills_feed as ff

    def stuck(a, b, coins=None, post=None):
        return {"fills": [], "complete": False, "last_block": None, "bytes": 0, "requests": 1}

    with pytest.raises(ff.FeedError):
        sph.read_day("2026-08-02", ["NEAR"], _Index(), deadline=float("inf"), stream=stuck)
    with pytest.raises(ff.FeedError):
        sph.read_day("2026-08-02", ["NEAR"], _Index(), deadline=0.0, stream=stuck)
