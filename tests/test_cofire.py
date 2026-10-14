"""Co-firing: who trades with him to the second, corrected for chance."""

from src import cofire

HIM = "0x45d26f28196d226497130c4bac709d808fed4029"
TWIN = "0x" + "2" * 40
COPIER = "0x" + "3" * 40
MM = "0x" + "4" * 40


def order(wallet, coin, side, t, size=1.0):
    return {"wallet": wallet, "coin": coin, "side": side, "base_size": size, "t": t,
            "first_px": 10.0, "taker": True}


def his_burst(coin, start, n, gap=1750):
    return [order(HIM, coin, "A", start + i * gap) for i in range(n)]


def test_a_twin_that_fires_in_the_same_seconds_is_a_hit_and_a_copier_is_not():
    state = cofire.empty_state()
    now = 0
    for w in range(3):
        base = 10_000_000 + w * 1_000_000
        orders = his_burst("ZEC", base, 12)
        # The twin fires 200 ms before each of his; the copier 1.5 s after.
        orders += [order(TWIN, "ZEC", "A", o["t"] - 200) for o in orders[:12]]
        orders += [order(COPIER, "ZEC", "A", o["t"] + 1500) for o in orders[:12]]
        # Window filler so the span is realistic (150 s).
        orders += [order(MM, "BTC", "B", base + i * 100) for i in range(1500)]
        cofire.fold_window(state, orders, {HIM}, now)
    hits = {h["wallet"]: h for h in cofire.screen(state)}
    assert TWIN in hits, hits
    assert hits[TWIN]["lead_share"] == 1.0 and hits[TWIN]["bursts"] == 3
    assert COPIER not in hits, "a wallet that only follows is a copier"
    assert cofire.lift(state["wallets"][COPIER]) >= cofire.MIN_LIFT, "it co-fires, it just follows"


def test_an_always_on_market_maker_is_explained_by_chance():
    state = cofire.empty_state()
    orders = his_burst("BTC", 1_000_000, 10)
    # The maker takes every 100 ms on BTC for the whole 150 s window, both sides.
    orders += [order(MM, "BTC", "A", 1_000_000 - 60_000 + i * 100) for i in range(1500)]
    cofire.fold_window(state, orders, {HIM}, 0)
    entry = state["wallets"][MM]
    assert entry["o"] > 0
    assert cofire.lift(entry) < cofire.MIN_LIFT, (entry["o"], entry["e"])
    assert cofire.screen(state) == []


def test_a_window_without_him_counts_nothing_against_anyone():
    state = cofire.empty_state()
    out = cofire.fold_window(state, [order(TWIN, "ZEC", "A", 5)], {HIM}, 0)
    assert out == {"his_orders": 0, "events": 0, "wallets": 0}
    assert state["windows"] == 1 and state["his_windows"] == 0 and state["wallets"] == {}


def test_side_and_coin_must_match():
    state = cofire.empty_state()
    orders = his_burst("ZEC", 1_000_000, 5)
    orders += [order(TWIN, "ZEC", "B", o["t"]) for o in orders[:5]]        # other side
    orders += [order(COPIER, "NEAR", "A", o["t"]) for o in orders[:5]]     # other coin
    cofire.fold_window(state, orders, {HIM}, 0)
    assert TWIN not in state["wallets"] and COPIER not in state["wallets"]


def test_cap_keeps_the_wallets_with_most_events():
    state = cofire.empty_state()
    for i in range(5):
        state["wallets"]["0x" + f"{i:040x}"] = {"o": i, "e": 0.0, "ld": 0, "ti": 0, "fo": 0,
                                                 "bs": [], "c": {}, "f": 0, "l": 0}
    cofire.cap(state, limit=2)
    assert sorted(int(e["o"]) for e in state["wallets"].values()) == [3, 4]


def test_summary_and_state_survive_a_json_round_trip():
    import json
    state = cofire.empty_state()
    orders = his_burst("ZEC", 1_000_000, 5) + [order(TWIN, "ZEC", "A", 1_000_050)]
    cofire.fold_window(state, orders, {HIM}, 7)
    again = json.loads(json.dumps(state))
    assert cofire.summary(again, cofire.screen(again))["wallets_with_events"] == 1
