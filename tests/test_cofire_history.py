"""Co-firing over his stored history: sessions, padded windows, controls, base rate."""

from src import cofire
from src import cofire_history as ch

HIM = "0x" + "a" * 40
KNOWN = "0x" + "b" * 40
TWIN = "0x" + "2" * 40
COPIER = "0x" + "3" * 40
MM = "0x" + "4" * 40


def fill(user, coin, t, h, sz="1", crossed=True, side="A"):
    return {"user": user, "coin": coin, "time": t, "hash": h, "sz": sz, "px": "10", "side": side,
            "crossed": crossed}


def order(wallet, coin, side, t):
    return {"wallet": wallet, "coin": coin, "side": side, "base_size": 1.0, "t": t,
            "first_px": 10.0, "taker": True}


def test_his_orders_group_crossing_fills_by_hash_and_drop_maker_fills():
    fills = [fill(HIM, "ZEC", 1000, "0x1"), fill(HIM, "ZEC", 1001, "0x1", sz="2"),
             fill(HIM, "ZEC", 5000, "0x2", crossed=False), fill(HIM, "BTC", 7000, "0x3", sz="0.1")]
    orders = ch.his_orders(fills, HIM)
    assert [(o["coin"], o["t"], o["base_size"]) for o in orders] == [("ZEC", 1000, 3.0), ("BTC", 7000, 0.1)]


def test_stored_fills_without_a_user_field_are_his():
    orders = ch.his_orders([{"coin": "ZEC", "time": 1, "hash": "0x1", "sz": "1", "crossed": True}], HIM)
    assert len(orders) == 1 and orders[0]["wallet"] == HIM


def test_sessions_split_on_the_gap_across_all_coins_and_carry_a_stable_key():
    orders = [order(HIM, "ZEC", "A", 0), order(HIM, "BTC", "B", 30_000), order(HIM, "ZEC", "A", 50_000),
              order(HIM, "NEAR", "A", 50_000 + ch.SESSION_GAP_MS + 1)]
    sessions = ch.sessions(orders)
    assert [s["coins"] for s in sessions] == [["BTC", "ZEC"], ["NEAR"]]
    assert sessions[0]["key"] == "0:BTC,ZEC" and sessions[0]["orders"] == 3
    assert sessions[0]["start"] == 0 and sessions[0]["end"] == 50_000
    # Re-deriving from the same orders gives the same keys (the census resumes by key).
    assert [s["key"] for s in ch.sessions(list(reversed(orders)))] == [s["key"] for s in sessions]


def test_slices_pad_every_window_and_split_a_long_session():
    short = {"start": 100_000, "end": 200_000}
    assert ch.slices(short, pad_ms=10_000, max_slice_ms=3_600_000) == [(90_000, 210_000)]
    long = {"start": 0, "end": 2_500_000}
    out = ch.slices(long, pad_ms=10_000, max_slice_ms=1_000_000)
    assert out == [(-10_000, 1_010_000), (990_001, 2_010_001), (1_990_002, 2_510_000)]


def _state_with_twin_and_copier():
    state = cofire.empty_state()
    for w in range(3):
        base = 10_000_000 + w * 1_000_000
        his = [order(HIM, "ZEC", "A", base + i * 1750) for i in range(12)]
        orders = list(his)
        orders += [order(TWIN, "ZEC", "A", o["t"] - 200) for o in his]
        orders += [order(KNOWN, "ZEC", "A", o["t"] - 100) for o in his]
        orders += [order(COPIER, "ZEC", "A", o["t"] + 1500) for o in his]
        orders += [order(MM, "BTC", "B", base + i * 100) for i in range(1500)]
        ch.fold_session(state, orders, HIM, base)
    return state


def test_known_wallets_are_controls_not_cluster():
    state = _state_with_twin_and_copier()
    controls = ch.controls(state, {KNOWN, "0x" + "c" * 40})
    by = {c["wallet"]: c for c in controls}
    assert by[KNOWN]["seen"] and by[KNOWN]["hit"] and by[KNOWN]["lead_share"] == 1.0
    assert by["0x" + "c" * 40] == {"wallet": "0x" + "c" * 40, "seen": False}
    # The known wallet is excluded from the stranger screen like the target.
    hits = {h["wallet"] for h in cofire.screen(state, exclude={HIM, KNOWN})}
    assert hits == {TWIN}


def test_base_rate_is_unmeasured_below_twenty_wallets_and_counts_hits_above():
    state = _state_with_twin_and_copier()
    rate = ch.base_rate(state, exclude={HIM, KNOWN})
    # The maker trades BTC while he fires ZEC: no events, so it is not in the population.
    assert rate["wallets"] == 2 and rate["hits"] == 1 and rate["hit_rate"] is None
    assert rate["lead_share_deciles"] is not None and rate["lift_deciles"] is not None
    # Pad the population with twenty makers explained by chance.
    for k in range(20):
        w = "0x" + f"{k:040x}"
        state["wallets"][w] = {"o": 30, "e": 29.0, "ld": 0, "ti": 0, "fo": 5, "bs": ["a", "b", "c"],
                               "c": {"BTC": 30}, "f": 0, "l": 0}
    rate = ch.base_rate(state, exclude={HIM, KNOWN})
    assert rate["wallets"] == 22 and rate["hits"] == 1
    assert abs(rate["hit_rate"] - 1 / 22) < 1e-9


def test_coverage_counts_sessions_and_orders_read():
    sessions = [{"key": "a", "start": 0, "end": 1, "orders": 10}, {"key": "b", "start": 5, "end": 6, "orders": 5}]
    out = ch.coverage({"his_orders": 9}, {"a"}, sessions)
    assert out == {"sessions": 2, "sessions_read": 1, "orders": 15, "orders_read": 10,
                   "first_ms": 0, "last_ms": 6, "his_orders_folded": 9}
    assert ch.coverage({}, set(), [])["first_ms"] is None


def test_by_coin_ranks_the_coins_hits_fire_on():
    hits = [{"coins": ["ZEC", "BTC"]}, {"coins": ["ZEC"]}]
    assert list(ch.by_coin({}, hits).items()) == [("ZEC", 2), ("BTC", 1)]


def test_the_digest_summarises_the_history_census():
    from src import digest
    doc = {"coverage": {"sessions_read": 40, "sessions": 294},
           "base_rate": {"wallets": 120, "hits": 2, "hit_rate": 2 / 120, "min_events": 10},
           "controls": [{"wallet": KNOWN, "seen": True, "hit": True}, {"wallet": "0x" + "c" * 40, "seen": False}],
           "hits": [{"wallet": TWIN}, {"wallet": COPIER}]}
    section = digest.cofire_history_section({"cofire_history": doc})
    assert section == {"sessions_read": 40, "sessions": 294, "hits": 2, "population": 120,
                       "hit_rate": 2 / 120, "controls_seen": 1, "controls_hit": 1, "controls": 2}
    assert digest.cofire_history_section({"cofire_history": None}) is None
