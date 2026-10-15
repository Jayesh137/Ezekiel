"""Client census: profiles from historicalOrders, his class, picking, base rate, split-half."""

from src import client_census as cc

A = "0x" + "a" * 40
B = "0x" + "b" * 40
C = "0x" + "c" * 40


def _o(t, tif="Ioc", coin="NEAR", side="B", cloid=None, sz="250.0", trigger=False, ro=False):
    return {"order": {"coin": coin, "side": side, "limitPx": "4.2", "sz": "0.0", "oid": t,
                      "timestamp": t, "isTrigger": trigger, "isPositionTpsl": False,
                      "reduceOnly": ro, "orderType": "Limit" if tif != "FrontendMarket" else "Market",
                      "origSz": sz, "tif": tif, "cloid": cloid}, "status": "filled"}


def _script(n=60, start=1_000_000, gap=1_700):
    return [_o(start + i * gap) for i in range(n)]


def test_round_size():
    assert cc.round_size("250.0") and cc.round_size("0.1") and cc.round_size("1000000")
    assert not cc.round_size("3.5121") and not cc.round_size("137")
    assert cc.round_size("0") is None and cc.round_size("x") is None


def test_profile_of_his_script_and_of_a_web_ui_account():
    rows = _script(60) + [_o(9_000_000 + i * 60_000, tif="FrontendMarket") for i in range(6)]
    p = cc.profile(rows)
    assert p["orders"] == 66 and p["measured"] and p["coins"] == 1
    assert abs(p["ioc_nocloid_share"] - 60 / 66) < 1e-4 and abs(p["frontend_share"] - 6 / 66) < 1e-4
    assert p["cloid_share"] == 0 and p["round_size_share"] == 1.0 and p["ioc_runs"] == 1
    assert cc.his_class(p) and cc.client_label(p) == "sdk_ioc_loop"
    web = cc.profile([_o(i * 60_000, tif="FrontendMarket") for i in range(60)])
    assert cc.client_label(web) == "web_ui" and not cc.his_class(web)
    bot = cc.profile([_o(i * 500, cloid="0xba5ed113124c5001" + "0" * 16) for i in range(60)])
    assert cc.client_label(bot) == "cloid_bot" and bot["cloid_prefixes"] == {"0xba5ed1": 60}
    assert cc.client_label(cc.profile(_script(10))) == "unmeasured"
    assert cc.profile([])["orders"] == 0


def test_ioc_runs_need_cadence_and_length_and_break_on_coin_side_or_gap():
    assert cc.ioc_runs([o["order"] for o in _script(15)]) == 1
    assert cc.ioc_runs([o["order"] for o in _script(14)]) == 0
    assert cc.ioc_runs([o["order"] for o in _script(30, gap=300)]) == 0          # too fast: a bot
    mixed = [o["order"] for o in _script(15)] + [o["order"] for o in _script(15, start=5_000_000)]
    assert cc.ioc_runs(mixed) == 2
    sides = [dict(o["order"], side="A" if i % 2 else "B") for i, o in enumerate(_script(30))]
    assert cc.ioc_runs(sides) == 0


def test_pick_wallets_orders_program_takers_then_registry_then_recent_and_skips_fresh_reads():
    population = {"wallets": {A: {"p": 0, "l": 5}, B: {"p": 2, "l": 1}, C: {"p": 0, "l": 9}}}
    profiles = {C: {"t": 100}}
    now = 100 + cc.RECHECK_MS - 1
    picked = cc.pick_wallets(population, {"0x" + "d" * 40: {}}, ["0x" + "e" * 40], profiles, now, limit=10)
    assert picked == [B, "0x" + "e" * 40, A, "0x" + "d" * 40]
    assert cc.pick_wallets(population, None, [], profiles, now + 2, limit=10) == [B, C, A]   # C stale again
    assert cc.pick_wallets(population, None, [], {}, now, limit=1) == [B]


def test_base_rate_and_compact_and_cap():
    his = cc.compact(cc.profile(_script(60)), 7)
    web = cc.compact(cc.profile([_o(i * 60_000, tif="FrontendMarket") for i in range(60)]), 8)
    unread = cc.compact(cc.profile(_script(5)), 9)
    assert his["his"] and his["lab"] == "sdk_ioc_loop" and his["t"] == 7
    rate = cc.base_rate({A: his, B: web, C: unread})
    assert rate["read"] == 3 and rate["measured"] == 2 and rate["his_class"] == 1 and rate["rate"] == 0.5
    assert rate["labels"] == {"sdk_ioc_loop": 1, "web_ui": 1} and 0.5 < rate["upper"] <= 1.0
    capped = cc.cap({A: his, B: web, C: unread}, limit=2)
    assert A in capped and len(capped) == 2        # his class is kept first


def test_split_half_reads_the_target_against_himself():
    rows = _script(60) + _script(60, start=50_000_000)
    out = cc.split_half(rows)
    assert out["measured"] and out["agree"] and out["older"]["his"] and out["newer"]["his"]
    assert cc.split_half(_script(60))["measured"] is False


def test_orders_sent_together_count_as_one_action_and_a_twice_listed_order_once():
    # Two same-coin orders per millisecond, 1.7 s apart: one action each, his cadence.
    rows = []
    for i in range(20):
        rows.append(_o(1_000_000 + i * 1_700))
        rows.append({**_o(1_000_000 + i * 1_700), "order": {**_o(1_000_000 + i * 1_700)["order"], "oid": 90_000 + i}})
    p = cc.profile(rows)
    assert p["orders"] == 40 and p["ioc_runs"] == 1 and p["orders_per_action"] == 2.0
    # The same oid listed twice (two status rows) is one order.
    assert cc.profile(_script(60) + _script(60))["orders"] == 60
