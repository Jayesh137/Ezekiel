"""Economic movements must not invent a second wallet from known self-funding."""

import json

import pytest

from src import correlator

TARGET = "0x" + "1" * 40
TREASURY = "0x" + "2" * 40
ROUTER = "0x" + "3" * 40
OTHER = "0x" + "4" * 40


def transfer(ref, src=TARGET, dst=OTHER, amount=1_000_000, **extra):
    return {"id": f"arbitrum:{ref}:erc20:0", "chain": "arbitrum", "tx_hash": ref,
            "src": src, "dst": dst, "amount_usd": amount, "ts": 1000, **extra}


def reconcile(records, ledger=None, decodes=None):
    from src.movements import reconcile_movements
    return reconcile_movements(records, ledger or [], {TARGET, TREASURY}, decodes or {})


def test_self_deposits_and_internal_transfers_are_resolved_but_onward_exit_remains():
    rows = [transfer("deposit", dst=ROUTER), transfer("internal", dst=TREASURY),
            transfer("onward", src=TREASURY)]
    result = reconcile(rows, decodes={"deposit": {"protocol": "cctp_extension",
                                                "hl_account": TARGET}})
    assert [r["ref"] for r in result["unresolved_exits"]] == ["onward"]
    assert {r["resolution"] for r in result["resolved"]} == {"cluster_internal", "bridge_to_cluster"}
    assert all(r["event_ids"] for r in result["movements"])


def test_unrelated_or_heuristic_decode_does_not_resolve_a_real_exit():
    for decodes in ({"another": {"hl_account": TARGET}},
                    {"exit": {"hl_account": TARGET, "heuristic": True}}):
        assert len(reconcile([transfer("exit", dst=ROUTER)], decodes=decodes)["unresolved_exits"]) == 1


def test_withdrawal_exact_payout_reference_resolves_only_the_withdrawal_leg():
    rows = [transfer("payout", src=ROUTER, dst=TARGET, withdrawal_hash="hl-withdraw"),
            transfer("onward")]
    ledger = [{"hash": "hl-withdraw", "time": 900_000,
               "delta": {"type": "withdraw", "usdc": "1000000"}}]
    result = reconcile(rows, ledger)
    assert [r["ref"] for r in result["unresolved_exits"]] == ["onward"]
    assert result["resolved"][0]["resolution"] == "withdrawal_to_cluster"
    assert "arbitrum:payout:erc20:0" in result["resolved"][0]["event_ids"]


def test_amount_similarity_alone_does_not_resolve_withdrawal():
    rows = [transfer("payout", src=ROUTER, dst=TARGET)]
    ledger = [{"hash": "hl-withdraw", "time": 1_000_000,
               "delta": {"type": "withdraw", "usdc": "1000000"}}]
    assert reconcile(rows, ledger)["unresolved_exits"][0]["ref"] == "hl-withdraw"


def test_conflicting_payout_destinations_remain_unresolved():
    rows = [transfer("p1", src=ROUTER, dst=TARGET, withdrawal_hash="w"),
            transfer("p2", src=ROUTER, dst=OTHER, withdrawal_hash="w")]
    ledger = [{"hash": "w", "time": 1_000_000,
               "delta": {"type": "withdraw", "usdc": "1000000", "destination": TARGET}}]
    result = reconcile(rows, ledger)
    assert result["unresolved_exits"][0]["ref"] == "w"
    assert result["unresolved_exits"][0]["resolution"] == "conflicting_payouts"


def test_malformed_hl_destination_cannot_resolve_a_bridge_exit():
    result = reconcile([transfer("exit", dst=ROUTER)], decodes={"exit": {"hl_account": "not-a-wallet"}})
    assert len(result["unresolved_exits"]) == 1


@pytest.mark.parametrize("amount", [None, "bad", float("nan"), float("inf"), -5])
def test_unpriced_invalid_and_nonpositive_values_never_seed_amount_match(amount):
    result = reconcile([transfer("unpriced", amount=amount)])
    assert result["unresolved_exits"] == []
    assert result["coverage"]["unpriced_or_invalid"] == 1


def test_transfer_identity_includes_chain_and_ledger_duplicates_collapse():
    rec = transfer("same")
    another = transfer("same", chain="base", id="base:same:erc20:0")
    result = reconcile([rec, rec, another])
    assert len(result["unresolved_exits"]) == 2


def test_collector_reads_known_cluster_and_existing_bridge_decodes(tmp_path, monkeypatch):
    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(correlator, "load_config", lambda: {"known_self_wallets": [TREASURY]})
    rows = {TARGET: [transfer("self", dst=ROUTER), transfer("internal", dst=TREASURY)],
            TREASURY: [transfer("onward", src=TREASURY)]}
    monkeypatch.setattr(correlator, "records_for", lambda wallet: rows.get(wallet, []))
    monkeypatch.setattr(correlator, "load_all_records", lambda path: [])
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "bridge_decodes.json").write_text(json.dumps({"self": {"hl_account": TARGET}}))
    assert [r["ref"] for r in correlator.collect_target_exits(TARGET, 100_000)] == ["onward"]


def test_rejected_approximate_deposit_cannot_consume_later_exact_match():
    exits = [{"amount": 1_000_000, "ts": 1000, "ref": "exit"}]
    entries = [{"amount": 975_000, "ts": 1001, "wallet": "approx"},
               {"amount": 1_000_000, "ts": 1002, "wallet": "exact"}]
    assert [r["wallet"] for r in correlator.find_correlations(exits, entries)] == ["exact"]


def test_assignment_is_permutation_invariant_and_keeps_alternative_hypotheses():
    exits = [{"amount": 1_000_000, "ts": 1000, "ref": "exit"}]
    entries = [{"amount": 990_000, "ts": 1001, "wallet": "early"},
               {"amount": 1_000_000, "ts": 1002, "wallet": "exact"}]
    a = correlator.find_correlations(exits, entries)
    b = correlator.find_correlations(exits, list(reversed(entries)))
    assert a[0]["wallet"] == b[0]["wallet"] == "exact"
    assert a[0]["alternatives"][0]["wallet"] == "early"
    assert a[0]["score_kind"] == "heuristic"


def test_global_assignment_does_not_let_one_stronger_edge_hide_two_better_combined():
    from src.matching import maximum_weight_pairs
    pairs, mode = maximum_weight_pairs([(0, 0, .9), (0, 1, .8), (1, 0, .8)])
    assert set(pairs) == {(0, 1), (1, 0)}
    assert mode == "exact"


def test_accounting_separates_classified_infrastructure_from_identified_recipient():
    from src.accounting import reconcile as account
    result = account([transfer("exchange")], TARGET, {OTHER: {"tier": "INFRASTRUCTURE"}}, set())
    assert result["destination_classified_share"] == 1
    assert result["controlled_recipient_share"] == 0
    assert result["unresolved_infrastructure_usd"] == 1_000_000
