"""The casebook's best suspects stay under every per-wallet detector (spec §12)."""

from src import roster
from src.casebook import store

T = "0x45d26f28196d226497130c4bac709d808fed4029"
W = "0x" + "e" * 40
A, B, C, D = ("0x" + c * 40 for c in "abcd")


def row(address, rank, central, on_hl=True, **extra):
    return {"address": address, "rank": rank, "central": central, "hl": {"on_hl": on_hl}, **extra}


INDEX = {"cases": [row(D, None, 2.0, known="config:known_self"),
                   row(A, 1, -1.0), row(B, 2, -2.5, on_hl=False), row(C, 3, -2.9),
                   row("0x" + "f" * 40, 4, -3.0)]}


def test_pins_are_unknown_hyperliquid_suspects_above_the_prior():
    assert roster.casebook_pins(8, INDEX) == [A, C]
    assert roster.casebook_pins(1, INDEX) == [A]
    assert roster.casebook_pins(8, {"cases": [row(A, 1, -1.0, ruling="not_him")]}) == []
    assert roster.casebook_pins(8, {"cases": [row(A, None, -1.0, excluded="service")]}) == []
    assert roster.casebook_pins(8, {"cases": "broken"}) == []


def test_pins_follow_the_operators_own_and_never_exceed_the_limit():
    config = {"target_wallet": T, "watch_wallets": [{"address": W}]}
    assert roster.detector_candidates(config, {"wallets": []}, 2, casebook=INDEX) == [W, A]
    picked = roster.detector_candidates(config, {"wallets": [{"wallet": B, "tier": "POSSIBLE"}]}, 40,
                                        casebook=INDEX)
    assert picked == [W, A, C, B]


def test_a_pin_the_operator_already_named_does_not_cost_a_slot():
    config = {"target_wallet": T, "watch_wallets": [{"address": A}]}
    assert roster.detector_candidates(config, {"wallets": []}, 40, casebook=INDEX) == [A, C]


def test_an_unreadable_or_missing_casebook_pins_nothing(tmp_path, monkeypatch):
    from src import utils
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    assert roster.casebook_pins(8) == []
    store.root(tmp_path).mkdir(parents=True)
    (store.root(tmp_path) / "latest.json").write_text("{")
    assert roster.casebook_pins(8) == []
    assert roster.detector_candidates({"target_wallet": T}, {"wallets": []}, 40) == []
