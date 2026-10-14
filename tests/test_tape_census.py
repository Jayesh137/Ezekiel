"""The tape census: a venue-wide execution census, screen and newcomer rule, read
off the public tape for free (src/tape_census.py, scripts/collect_tape.py).

A 150 s window sees hundreds of takers on his markets and used to keep only an
exact-clip burst. The sketch keeps sizes per coin per wallet across windows, so the
census can measure how often a taker of his markets carries a comparable clip
table, screen every sketch for his table or its rank structure, and judge a
newcomer in size on a rare market against his silences by its REAL birth.
"""

import pytest

from scripts import collect_tape as ct
from src import tape
from src import tape_census as tc

DAY = 86_400_000
TARGET = {
    "clip_table": {"ZEC": {"size": 1}, "NEAR": {"size": 250}, "xyz:SILVER": {"size": 20},
                   "BTC": {"size": 0.1}, "XRP": {"size": 1000}},
    "clip_notionals": {"ZEC": 300.0, "NEAR": 1000.0, "xyz:SILVER": 1000.0,
                       "BTC": 10000.0, "XRP": 2500.0},
}
OTHER = "0x" + "9" * 40


def _w(i):
    return f"0x{i:040x}"


def _trade(h, buyer, seller, side, coin, px, sz, t):
    return {"hash": h, "users": [buyer, seller], "side": side, "coin": coin,
            "px": str(px), "sz": str(sz), "time": t, "tid": h}


def _burst(wallet, coin, clip, n, px=4.0, t0=0, gap_ms=1750, h0=1):
    return [_trade(h0 + i, OTHER, wallet, "A", coin, px, clip, t0 + i * gap_ms) for i in range(n)]


def _orders(trades):
    return tape.taker_orders(trades)


def test_fold_window_keeps_sizes_per_coin_and_first_last_seen():
    sk = tc.empty_sketches()
    out = tc.fold_window(sk, _orders(_burst(_w(1), "ZEC", 1, 10)), now_ms=5_000)
    entry = sk["wallets"][_w(1)]
    assert entry["f"] == 5_000 and entry["l"] == 5_000 and entry["w"] == 1
    assert entry["c"]["ZEC"] == {"n": 10, "s": {"1": 10}}
    assert out["first_seen"] == [_w(1)]
    assert out["notional"][_w(1)] == pytest.approx(40.0)
    assert sk["prices"]["ZEC"] == 4.0
    tc.fold_window(sk, _orders(_burst(_w(1), "ZEC", 1, 3, t0=99_000, h0=500)), now_ms=9_000)
    assert entry["f"] == 5_000 and entry["l"] == 9_000 and entry["w"] == 2
    assert entry["c"]["ZEC"]["n"] == 13


def test_a_constant_clip_survives_the_size_cap_and_scattered_sizes_do_not_form_one():
    sk = tc.empty_sketches()
    trades = _burst(_w(1), "ZEC", 1, 12)
    trades += [_trade(900 + i, OTHER, _w(1), "A", "ZEC", 4.0, 3 + i * 0.37, 90_000 + i * 1000)
               for i in range(10)]                       # ten one-off sizes after the clip
    tc.fold_window(sk, _orders(trades), now_ms=1)
    slot = sk["wallets"][_w(1)]["c"]["ZEC"]
    assert len(slot["s"]) <= tc.MAX_SIZES_PER_COIN
    assert slot["s"]["1"] == 12                           # the dominant size is never evicted
    table = tc.clip_table(sk["wallets"][_w(1)])
    assert "ZEC" not in table                              # 12 of 22 is under the 80% bar
    sk2 = tc.empty_sketches()
    tc.fold_window(sk2, _orders(_burst(_w(2), "ZEC", 1, 12)), now_ms=1)
    assert tc.clip_table(sk2["wallets"][_w(2)])["ZEC"]["size"] == 1


def test_market_counts_are_distinct_wallets_per_market():
    sk = tc.empty_sketches()
    trades = _burst(_w(1), "ZEC", 1, 3) + _burst(_w(2), "ZEC", 2, 3, h0=100) + _burst(_w(2), "BTC", 0.5, 2, h0=200)
    tc.fold_window(sk, _orders(trades), now_ms=1)
    assert tc.market_counts(sk) == {"ZEC": 2, "BTC": 1}


def _sketch_with(wallet, clips, n=10):
    trades, h = [], 1
    for coin, (size, px) in clips.items():
        trades += _burst(wallet, coin, size, n, px=px, h0=h)
        h += 1000
    return trades


def test_census_counts_judgeable_and_comparable_and_screens_for_his_table():
    sk = tc.empty_sketches()
    trades = []
    # Reproduces four of his sizes: a hit by the exact path.
    trades += _sketch_with(_w(1), {"ZEC": (1, 300), "NEAR": (250, 4), "xyz:SILVER": (20, 50), "BTC": (0.1, 100000)})
    # Comparable (clips on two of his coins) at other sizes: a measured non-match.
    trades += _sketch_with(_w(2), {"ZEC": (5, 300), "NEAR": (100, 4)})
    # Active on his coins but no constant clip: judgeable, not comparable.
    scattered = [_trade(5000 + i, OTHER, _w(3), "A", "ZEC", 300, 1 + i, 1000 * i) for i in range(10)]
    scattered += [_trade(6000 + i, OTHER, _w(3), "A", "NEAR", 4, 10 + i, 1000 * i) for i in range(10)]
    trades += scattered
    # Only one of his coins: not judgeable.
    trades += _sketch_with(_w(4), {"ZEC": (1, 300)})
    tc.fold_window(sk, _orders(trades), now_ms=1)
    census = tc.census(sk, TARGET)
    assert census["attempted"] == 3                       # w1, w2, w3 judgeable
    assert census["population"] == 2                      # w1, w2 comparable
    assert census["comparable_upper"] is None             # far under MIN_CENSUS_ATTEMPTS
    assert [h["wallet"] for h in census["hits"]] == [_w(1)]
    assert census["hits"][0]["clips_matched"] == 4
    assert census["hits"][0]["clips_matched_coins"] == ["BTC", "NEAR", "ZEC", "xyz:SILVER"]


def test_a_rescaled_program_is_a_hit_by_rank_structure():
    sk = tc.empty_sketches()
    # Every clip halved: no exact size matches, but the per-coin notional ranking is his.
    trades = _sketch_with(_w(1), {"ZEC": (0.5, 300), "NEAR": (125, 4), "xyz:SILVER": (10, 50),
                                  "BTC": (0.05, 100000), "XRP": (500, 2.5)})
    tc.fold_window(sk, _orders(trades), now_ms=1)
    census = tc.census(sk, TARGET)
    assert census["hits"] and census["hits"][0]["clips_matched"] == 0
    assert census["hits"][0]["notional_structure_rho"] == 1.0
    assert census["hits"][0]["notional_coins_compared"] == 5


def test_the_cluster_is_excluded_from_the_census():
    sk = tc.empty_sketches()
    me = _w(42)
    tc.fold_window(sk, _orders(_sketch_with(me, {"ZEC": (1, 300), "NEAR": (250, 4), "BTC": (0.1, 100000)})), now_ms=1)
    assert tc.census(sk, TARGET, exclude={me})["attempted"] == 0
    assert tc.census(sk, TARGET)["hits"][0]["wallet"] == me


def test_newcomers_need_size_a_rare_market_and_a_first_sighting():
    window = {"first_seen": [_w(1), _w(2), _w(3)],
              "notional": {_w(1): 60_000, _w(2): 500, _w(3): 90_000},
              "markets": {_w(1): ["xyz:SILVER", "BTC"], _w(2): ["xyz:SILVER"], _w(3): ["BTC"]}}
    counts = {"xyz:SILVER": 12, "BTC": 900}
    rows = tc.newcomers(window, counts, set(TARGET["clip_table"]))
    assert [r["wallet"] for r in rows] == [_w(1)]          # w2 too small, w3 only a busy market
    assert rows[0]["rare_markets"] == ["xyz:SILVER"]
    assert tc.newcomers(window, counts, set(TARGET["clip_table"]), exclude={_w(1)}) == []


def test_born_in_silence_reads_closed_gaps_and_the_open_one():
    target_days = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 140]   # a 20-day gap
    closed = tc.born_in_silence(target_days, 122, now_day=150, state=None)
    assert closed["score"] > 0 and closed["open"] is False and closed["gap_length"] == 20
    # Born while he is quiet NOW (unusual against his p90), two days into it.
    state = {"unusual": True, "last_active_day": 140}
    live = tc.born_in_silence(target_days, 142, now_day=150, state=state)
    assert live["score"] > 0 and live["open"] is True and live["delay_days"] == 2
    # Normal rhythm: a wallet born after his last day is just a wallet.
    assert tc.born_in_silence(target_days, 142, now_day=150, state={"unusual": False, "last_active_day": 140})["score"] == 0
    # Older than the silence: not born in it.
    assert tc.born_in_silence(target_days, 139, now_day=150, state=state)["score"] == 0
    # Too long after the silence began: not a handoff.
    assert tc.born_in_silence(target_days, 148, now_day=150, state=state)["score"] == 0


def test_cap_evicts_oldest_but_keeps_comparable_sketches_longest():
    sk = tc.empty_sketches()
    for i in range(1, 6):
        tc.fold_window(sk, _orders(_burst(_w(i), "WIF", 7, 2, h0=i * 10)), now_ms=i)   # passers-by, oldest first
    old_comparable = _w(99)
    tc.fold_window(sk, _orders(_sketch_with(old_comparable, {"ZEC": (3, 300), "NEAR": (9, 4)})), now_ms=0)
    tc.cap(sk, limit=3, target_coins=set(TARGET["clip_table"]))
    kept = set(sk["wallets"])
    assert old_comparable in kept                           # oldest of all, kept for its clips
    assert kept - {old_comparable} == {_w(4), _w(5)}       # the newest passers-by


def test_judge_newcomers_reads_real_births_registers_handoffs_and_alerts(tmp_path):
    from src.candidate_registry import iter_candidates
    born_in = _w(1)
    born_out = _w(2)
    rows = [{"wallet": born_in, "notional_usd": 90_000, "rare_markets": ["xyz:SILVER"]},
            {"wallet": born_out, "notional_usd": 50_000, "rare_markets": ["xyz:SILVER"]},
            {"wallet": "nope", "notional_usd": 1}]
    births = {born_in: 142 * DAY + 5, born_out: 50 * DAY}

    def fetch(body):
        return {"ok": True, "data": [["allTime", {"accountValueHistory": [[births[body["user"]], "1000"]]}]]}

    alerted = []
    state = {"unusual": True, "last_active_day": 140}
    out = ct.judge_newcomers(rows, [100, 120, 140], 150, state, fetch=fetch, data_dir=tmp_path,
                             alert=lambda w, d: alerted.append((w, d)))
    assert [h["wallet"] for h in out["handoffs"]] == [born_in]
    assert alerted and alerted[0][0] == born_in and alerted[0][1]["open"] is True
    assert [r["wallet"] for r in iter_candidates(tmp_path)] == [born_in]
    assert "tape_handoff" in iter_candidates(tmp_path)[0]["discovery_sources"]
    assert out["unread"] == [] and out["deferred"] == 0


def test_an_unreadable_birth_is_unread_never_not_a_handoff(tmp_path):
    rows = [{"wallet": _w(1), "notional_usd": 90_000, "rare_markets": ["xyz:SILVER"]}]
    out = ct.judge_newcomers(rows, [100, 140], 150, {"unusual": True, "last_active_day": 140},
                             fetch=lambda body: {"ok": False, "error": "timeout"}, data_dir=tmp_path)
    assert out["judged"] == [] and out["handoffs"] == []
    assert out["unread"][0]["wallet"] == _w(1)


def test_judge_newcomers_is_bounded_largest_first():
    rows = [{"wallet": _w(i), "notional_usd": 100_000 - i} for i in range(1, 15)]
    seen = []

    def fetch(body):
        seen.append(body["user"])
        return {"ok": False, "error": "x"}

    out = ct.judge_newcomers(rows, [1], 2, None, fetch=fetch, limit=3)
    assert seen == [_w(1), _w(2), _w(3)] and out["deferred"] == 11


def test_fold_and_census_end_to_end_with_a_fake_portfolio(tmp_path):
    config = {"target_wallet": _w(7), "known_self_wallets": [_w(8)], "excluded_addresses": []}
    trades = _sketch_with(_w(1), {"ZEC": (1, 300), "NEAR": (250, 4), "xyz:SILVER": (20, 50)}, n=12)
    trades += _sketch_with(_w(7), {"ZEC": (1, 300), "NEAR": (250, 4)}, n=12)     # him: excluded
    reads = []

    def fetch(body):
        reads.append(body)
        return {"ok": True, "data": [["allTime", {"accountValueHistory": [[141 * DAY, "5"]]}]]}

    out = ct.fold_and_census(trades, TARGET, config, now_ms=150 * DAY, fetch=fetch, data_dir=tmp_path,
                             sketches=tc.empty_sketches(), state={"unusual": True, "last_active_day": 140},
                             target_days=[100, 120, 140])
    assert _w(7) not in out["sketches"]["wallets"]
    assert out["census"]["hits"][0]["wallet"] == _w(1)
    assert all(b["type"] == "portfolio" for b in reads)
    assert out["judged"]["handoffs"][0]["wallet"] == _w(1)
    assert out["judged"]["handoffs"][0]["birth_day"] == 141


def test_register_census_hits_makes_each_a_candidate(tmp_path):
    from src.candidate_registry import iter_candidates
    hits = [{"wallet": _w(1), "clips_matched": 4, "clips_compared": 4, "clip_match_ratio": 1.0},
            {"wallet": "bad"}]
    assert ct.register_census_hits(hits, data_dir=tmp_path) == [_w(1)]
    row = iter_candidates(tmp_path)[0]
    assert "tape_census" in row["discovery_sources"]


def test_load_sketches_refuses_a_damaged_memory_but_starts_empty_on_none(tmp_path, monkeypatch):
    monkeypatch.setattr(ct, "SKETCH_FILE", tmp_path / "sketches.json")
    assert ct.load_sketches() == tc.empty_sketches()
    (tmp_path / "sketches.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ct.load_sketches()
    (tmp_path / "sketches.json").write_text('{"wallets": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        ct.load_sketches()


def test_registry_leads_get_a_reserved_slice_of_the_detector():
    from scripts import check_execution_program as chk
    roster = [f"0x{i:040x}" for i in range(100, 160)]
    registry_rows = [
        {"wallet": _w(1), "discovery_sources": ["tape_census"], "last_positive_evidence": "2026-10-12T00:00:00"},
        {"wallet": _w(2), "discovery_sources": ["funding_route"], "last_positive_evidence": "2026-10-12T00:00:00"},
        {"wallet": _w(3), "discovery_sources": ["tape_handoff"], "last_positive_evidence": "2026-10-11T00:00:00"},
    ]
    leads = chk.registry_candidates(registry_rows)
    assert leads == [_w(1), _w(3)]                        # newest first, other sources ignored
    picked = chk.select_candidates(roster, None, {"ZEC"}, cap=60, registry=leads)
    assert _w(1) in picked and _w(3) in picked and len(picked) == 60
    assert picked[0] in roster                            # roster leads still lead


def test_a_rare_market_is_read_relative_to_his_busiest_one():
    from src import tape_census as tc
    counts = {"BTC": 1100, "xyz:MU": 103, "xyz:COPPER": 51, "BLAST": 15}
    coins = set(counts)
    assert tc.rare_bar(counts, coins) == 110
    assert tc.rare_markets(counts, coins) == ["BLAST", "xyz:COPPER", "xyz:MU"]
    # A small sketch keeps the fixed bar.
    assert tc.rare_bar({"BTC": 100, "xyz:MU": 12}, {"BTC", "xyz:MU"}) == tc.RARE_MARKET_TAKERS
