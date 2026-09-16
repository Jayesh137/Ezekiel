# tests/test_bridge_destination_scope.py
"""Whose bridging is decoded, and when a foreign landing is news.

Config alone left `0xf078969e…` — CONFIRMED, two-way with the target — with
nothing reading its bridge calldata while it sent $32.8M through Circle to
Monad (domain 15, which the decoder could not even name) in the month to
2026-09-15. And every historical foreign row re-alerted CRITICAL each time its
72h cooldown lapsed: the alert record held the same Socket and Solana rows
again and again.
"""

import scripts.check_bridge_destinations as check
from src.chain.bridges import CCTP_DOMAINS

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TREASURY = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
F = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
CONFIG = {"target_wallet": T, "known_self_wallets": [TREASURY]}


def test_domain_15_is_monad_and_19_hyperevm():
    assert CCTP_DOMAINS[15] == "monad"
    assert CCTP_DOMAINS[19] == "hyperevm"
    assert CCTP_DOMAINS[3] == "arbitrum"


def test_confirmed_roster_wallets_are_decoded_but_kept_apart_from_config():
    roster = {"wallets": [{"wallet": F, "tier": "CONFIRMED"},
                          {"wallet": "0x" + "11" * 20, "tier": "PROBABLE"},
                          {"wallet": "0x" + "22" * 20, "tier": "CONFIRMED", "is_service": True},
                          {"wallet": TREASURY, "tier": "CONFIRMED"}]}
    cluster, confirmed = check.decoded_wallets(CONFIG, roster)
    assert cluster == sorted([T, TREASURY])
    assert confirmed == [F]


def _row(src, tx, recipient="0x" + "ee" * 20):
    return {"src": src, "tx_hash": tx, "protocol": "cctp", "destination_chain": "monad",
            "recipient": recipient, "hl_account": None, "amount_usd": 7_000_000.0}


def test_a_foreign_row_is_news_once_for_a_wallet_already_decoded():
    previous = {"decoded_wallets": [T, TREASURY, F], "foreign": [_row(F, "0xold")]}
    report = {"foreign": [_row(F, "0xold"), _row(F, "0xnew")]}
    alert, baseline = check.foreign_to_alert(previous, report, [T, TREASURY])
    assert [r["tx_hash"] for r in alert] == ["0xnew"]
    assert baseline == []


def test_a_newly_decoded_wallet_brings_its_history_as_a_baseline():
    previous = {"decoded_wallets": [T, TREASURY], "foreign": []}
    report = {"foreign": [_row(F, "0xhistory")]}
    alert, baseline = check.foreign_to_alert(previous, report, [T, TREASURY])
    assert alert == [] and [r["tx_hash"] for r in baseline] == ["0xhistory"]


def test_a_legacy_report_counts_the_config_cluster_as_already_decoded():
    """The report written before this field existed decoded config only."""
    previous = {"foreign": [_row(T, "0xseen")]}
    report = {"foreign": [_row(T, "0xseen"), _row(T, "0xfresh"), _row(F, "0xhistory")]}
    alert, baseline = check.foreign_to_alert(previous, report, [T, TREASURY])
    assert [r["tx_hash"] for r in alert] == ["0xfresh"]
    assert [r["tx_hash"] for r in baseline] == ["0xhistory"]


def test_an_undelivered_alert_is_retried_even_though_it_was_seen():
    stuck = _row(T, "0xstuck")
    previous = {"decoded_wallets": [T], "foreign": [stuck], "undelivered_foreign": [stuck]}
    alert, _ = check.foreign_to_alert(previous, {"foreign": [stuck]}, [T])
    assert [r["tx_hash"] for r in alert] == ["0xstuck"]


def test_no_previous_report_alerts_config_rows_and_baselines_the_rest():
    alert, baseline = check.foreign_to_alert(None, {"foreign": [_row(T, "0x1"), _row(F, "0x2")]},
                                             [T, TREASURY])
    assert [r["tx_hash"] for r in alert] == ["0x1"]
    assert [r["tx_hash"] for r in baseline] == ["0x2"]
