"""The client census shell: reads, failures, controls, with fakes."""

from scripts import census_client_fingerprint as cs
from src import client_census as cc

A = "0x" + "a" * 40
B = "0x" + "b" * 40
T = "0x" + "1" * 40
K = "0x" + "2" * 40


def _o(t, tif="Ioc", cloid=None):
    return {"order": {"coin": "NEAR", "side": "B", "limitPx": "4", "sz": "0", "oid": t, "timestamp": t,
                      "isTrigger": False, "isPositionTpsl": False, "reduceOnly": False,
                      "orderType": "Limit", "origSz": "250", "tif": tif, "cloid": cloid}, "status": "filled"}


def _fetch(answers):
    def fetch(body):
        a = answers.get(body["user"])
        if a is None:
            return {"ok": False, "data": None, "error": "timeout"}
        return {"ok": True, "data": a, "error": None}
    return fetch


def test_census_profiles_each_wallet_and_names_his_class_and_failures():
    answers = {A: [_o(1_000_000 + i * 1_700) for i in range(60)],
               B: [_o(i * 60_000, tif="FrontendMarket") for i in range(60)]}
    profiles = {}
    out = cs.census([A, B, "0x" + "c" * 40], profiles, now_ms=5, fetch=_fetch(answers), log=lambda *a: None)
    assert out["read"] == [A, B] and out["his_class"] == [A]
    assert out["failed"] == [{"wallet": "0x" + "c" * 40, "error": "timeout"}]
    assert profiles[A]["his"] and profiles[B]["lab"] == "web_ui" and "0x" + "c" * 40 not in profiles


def test_census_stops_when_the_budget_is_spent():
    class Budget:
        def __init__(self):
            self.n = 0

        def can_continue(self):
            self.n += 1
            return self.n <= 1
    answers = {A: [_o(i) for i in range(60)], B: [_o(i) for i in range(60)]}
    out = cs.census([A, B], {}, now_ms=1, fetch=_fetch(answers), budget=Budget(), log=lambda *a: None)
    assert out["read"] == [A]


def test_controls_read_known_wallets_once_per_fortnight_and_split_the_target():
    config = {"target_wallet": T, "known_self_wallets": [K]}
    answers = {T: [_o(1_000_000 + i * 1_700) for i in range(60)] + [_o(9_000_000 + i * 1_700) for i in range(60)],
               K: [_o(i * 60_000, tif="FrontendMarket") for i in range(60)]}
    profiles = {}
    out = cs.controls(config, profiles, now_ms=10, fetch=_fetch(answers))
    assert out["known"][K]["lab"] == "web_ui" and out["target_split_half"]["agree"] is True
    # A fresh reading is reused, not re-read.
    calls = []

    def counting(body):
        calls.append(body["user"])
        return _fetch(answers)(body)
    cs.controls(config, profiles, now_ms=10 + cc.RECHECK_MS - 1, fetch=counting)
    assert calls == [T]
