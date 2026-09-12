# tests/test_detector_candidates.py
"""Who the per-wallet detectors get to ask about.

The roster ranks by how much evidence a wallet ALREADY has. Four detectors —
dormancy, co-movement, portfolio overlap and agent links — then took the first
N of that ranking, which inverts the search: a wallet with no vectors yet sorts
below every wallet that has one, and a freshly migrated wallet has no vectors by
construction. Measured live 2026-09-12, `0xdd53c529…` — the single wallet named
in `config.watch_wallets`, PROBABLE on amount and timing, under close watch —
sat at position 166 of 180 and was cut by all four caps of 40. The cut-off was a
wallet with confidence 0.0311.

This is the `expanded_ledger` rule again: when a cap trims a collection, ask what
the sort order MEANS. Here it means "evidence already found", and it was being
used to choose where to look for evidence not yet found.
"""

import json

import pytest

from src import roster


def _roster(*rows):
    return {"wallets": [{"wallet": w, "tier": t, "confidence": c,
                         "is_service": t == "INFRASTRUCTURE"}
                        for w, t, c in rows]}


TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
WATCHED = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
SELF = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"


def test_an_operator_watched_wallet_survives_a_cap_that_would_cut_it():
    config = {"target_wallet": TARGET,
              "watch_wallets": [{"address": WATCHED, "why": "born in his silence"}]}
    rs = _roster(*[(f"0x{i:040x}", "POSSIBLE", 0.4) for i in range(1, 60)],
                 (WATCHED, "WATCH", 0.0))

    picked = roster.detector_candidates(config, rs, limit=40)

    assert picked[0] == WATCHED
    assert len(picked) == 40


def test_a_known_self_wallet_is_asked_about_too():
    config = {"target_wallet": TARGET, "known_self_wallets": [SELF]}
    picked = roster.detector_candidates(config, _roster(("0xaa", "POSSIBLE", 0.9)), limit=10)
    assert picked == [SELF, "0xaa"]


def test_the_operators_list_comes_before_ground_truth():
    config = {"target_wallet": TARGET, "known_self_wallets": [SELF],
              "watch_wallets": [{"address": WATCHED}]}
    picked = roster.detector_candidates(config, _roster(), limit=10)
    assert picked == [WATCHED, SELF]


def test_the_target_is_never_a_candidate_for_himself():
    config = {"target_wallet": TARGET, "watch_wallets": [{"address": TARGET}],
              "known_self_wallets": [TARGET]}
    assert roster.detector_candidates(config, _roster((TARGET, "CONFIRMED", 1.0)), limit=10) == []


def test_infrastructure_is_still_skipped():
    rs = _roster(("0xexchange", "INFRASTRUCTURE", 0.99), ("0xreal", "POSSIBLE", 0.1))
    assert roster.detector_candidates({"target_wallet": TARGET}, rs, limit=10) == ["0xreal"]


def test_roster_order_is_preserved_after_the_pinned_wallets():
    config = {"target_wallet": TARGET, "watch_wallets": [{"address": WATCHED}]}
    rs = _roster(("0xstrong", "CONFIRMED", 0.9), ("0xweak", "POSSIBLE", 0.1))
    assert roster.detector_candidates(config, rs, limit=10) == [WATCHED, "0xstrong", "0xweak"]


def test_a_pinned_wallet_already_high_on_the_roster_is_not_listed_twice():
    config = {"target_wallet": TARGET, "watch_wallets": [{"address": "0xaa"}]}
    picked = roster.detector_candidates(config, _roster(("0xaa", "CONFIRMED", 0.9)), limit=10)
    assert picked == ["0xaa"]


def test_a_bare_string_watch_entry_is_accepted():
    config = {"target_wallet": TARGET, "watch_wallets": [WATCHED]}
    assert roster.detector_candidates(config, _roster(), limit=10) == [WATCHED]


def test_pinned_wallets_are_never_dropped_even_when_they_exceed_the_limit():
    """The cap protects the API budget, but silence about a wallet the operator
    named by hand is not a saving — it is the blindness this fixes."""
    config = {"target_wallet": TARGET,
              "watch_wallets": [{"address": f"0x{i:040x}"} for i in range(1, 6)]}
    picked = roster.detector_candidates(config, _roster(("0xaa", "POSSIBLE", 0.5)), limit=2)
    assert len(picked) == 5 and "0xaa" not in picked


def test_a_missing_roster_still_yields_the_operators_wallets():
    config = {"target_wallet": TARGET, "watch_wallets": [{"address": WATCHED}]}
    assert roster.detector_candidates(config, None, limit=40) == [WATCHED]


@pytest.mark.parametrize("script,attr", [
    ("scripts.check_dormancy", "candidate_wallets"),
    ("scripts.check_comovement", "candidate_wallets"),
    ("scripts.check_portfolio_overlap", "candidate_wallets"),
    ("scripts.check_agents", "wallets_to_check"),
])
def test_every_per_wallet_detector_pins_the_watched_wallet(script, attr, tmp_path, monkeypatch):
    """The regression that made this necessary, asserted at each call site."""
    import importlib

    mod = importlib.import_module(script)
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps(
        _roster(*[(f"0x{i:040x}", "POSSIBLE", 0.4) for i in range(1, 200)],
                (WATCHED, "WATCH", 0.0))))
    (tmp_path / "newborn").mkdir(parents=True)
    (tmp_path / "newborn" / "latest.json").write_text(json.dumps({"newborn": []}))

    picked = getattr(mod, attr)({"target_wallet": TARGET,
                                 "watch_wallets": [{"address": WATCHED}]})

    assert WATCHED in picked, f"{script} cut the wallet under close watch"
