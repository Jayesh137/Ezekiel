# tests/test_trace_patterns.py
"""The trace engine's pure detectors, on real Hyperliquid ledger rows.

The fixture holds three ledgers read from the live API on 2026-10-04:
`0xf078969e…` (a config cluster wallet; its rows other than one UENA airdrop
distributor), `0x4aecac3b…` (two rows: received 140,777 UENA from
`0xf078969e…`, forwarded the identical amount 11 seconds later) and the first
300 rows of `0x1f6093d3…`, the hub it forwarded to.
"""

import json
from pathlib import Path

from src.trace import hl_ledger, patterns, value

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "trace" / "hl_ledgers.json").read_text())
F07 = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
DEP = "0x4aecac3b90dd0ad50d274c19221874e5ba8a4d45"
HUB = "0x1f6093d33db935b2ebd81d23312da5f11759973e"


# --- normalising ledger rows -----------------------------------------------------

def test_a_spot_transfer_becomes_an_edge_valued_by_usdc_value_not_quantity():
    edges = hl_ledger.normalise(FIXTURE[F07], F07)
    out = [e for e in edges if e["src"] == F07 and e["dst"] == DEP]
    assert len(out) == 1
    e = out[0]
    assert e["chain"] == "hyperliquid" and e["kind"] == "spotTransfer"
    assert e["asset"] == "UENA"
    assert e["amount"] == 140777.0079
    # Rule 11: the dollar value is usdcValue, never the token quantity.
    assert e["amount_usd"] == 24429.03418


def test_a_bridge_deposit_is_not_a_transfer_between_two_accounts():
    edges = hl_ledger.normalise(FIXTURE[F07], F07)
    assert all(e["kind"] != "deposit" for e in edges)


def test_a_token_with_no_dollar_value_is_unpriced_not_zero():
    row = {"time": 1, "hash": "0xh", "delta": {"type": "spotTransfer", "token": "MAX",
           "amount": "1000000000", "usdcValue": "0.0", "user": "0xa", "destination": F07}}
    (e,) = hl_ledger.normalise([row], F07)
    assert e["amount_usd"] is None


def test_normalising_twice_yields_the_same_ids():
    a = [e["id"] for e in hl_ledger.normalise(FIXTURE[F07], F07)]
    b = [e["id"] for e in hl_ledger.normalise(FIXTURE[F07], F07)]
    assert a == b and len(set(a)) == len(a)


def test_vault_flows_name_the_vault_as_the_counterparty():
    owner = "0x" + "a" * 40
    vault = "0x" + "b" * 40
    rows = [{"time": 1, "hash": "0x1", "delta": {"type": "vaultDeposit", "vault": vault, "usdc": "500.0"}},
            {"time": 2, "hash": "0x2", "delta": {"type": "vaultWithdraw", "vault": vault, "user": owner,
                                                  "netWithdrawnUsd": "510.0"}}]
    edges = hl_ledger.normalise(rows, owner)
    assert [(e["src"], e["dst"], e["amount_usd"]) for e in edges] == [
        (owner, vault, 500.0), (vault, owner, 510.0)]


# --- hubs and deposit addresses inside Hyperliquid ------------------------------

def test_a_hub_is_recognised_from_its_own_ledger():
    assert patterns.is_hub(FIXTURE[HUB], HUB) is True
    assert patterns.is_hub(FIXTURE[DEP], DEP) is False
    assert patterns.is_hub(FIXTURE[F07], F07) is False


def test_a_hypercore_deposit_address_is_named_with_its_hub():
    assert patterns.deposit_hub(FIXTURE[DEP], DEP) == HUB


def _deposit_rows(gap_s=11, out_amount="140777.0079", second_dest=None):
    t0 = 1_769_276_590_000
    rows = [{"time": t0, "hash": "0xin", "delta": {"type": "spotTransfer", "token": "UENA",
             "amount": "140777.0079", "usdcValue": "24429.0", "user": F07, "destination": DEP}},
            {"time": t0 + gap_s * 1000, "hash": "0xout", "delta": {"type": "spotTransfer", "token": "UENA",
             "amount": out_amount, "usdcValue": "24429.0", "user": DEP, "destination": HUB}}]
    if second_dest:
        rows.append({"time": t0 + 20_000, "hash": "0xout2", "delta": {"type": "spotTransfer", "token": "UENA",
                     "amount": "1", "usdcValue": "1", "user": DEP, "destination": second_dest}})
    return rows


def test_a_slow_forward_is_not_a_deposit_address():
    assert patterns.deposit_hub(_deposit_rows(gap_s=3 * 3600), DEP) is None


def test_a_partial_forward_is_not_a_deposit_address():
    assert patterns.deposit_hub(_deposit_rows(out_amount="100000"), DEP) is None


def test_forwarding_to_two_places_is_not_a_deposit_address():
    assert patterns.deposit_hub(_deposit_rows(second_dest="0x" + "c" * 40), DEP) is None


# --- two of his wallets paying the same quiet address ---------------------------

def _edge(src, dst, usd, ts):
    return {"id": f"{src}{dst}{ts}", "src": src, "dst": dst, "amount_usd": usd, "ts": ts,
            "chain": "ethereum"}


PAYEE = "0x734c92135eb462b8ba5a5edfe4000c5d7f6a6ec4"
COFUNDER = "0x793a3e8a1f0e19719bfa39288436958bcd475dc4"
DAY = 86400


def test_an_outsider_paying_a_quiet_wallet_his_wallet_also_paid_is_linked():
    # 2025-09-20, hand-traced: 0x793a3e8a funded 0x734c9213 (gas + $3.5M) and
    # 0xf078969e paid it $2M two hours later; it put all $5.5M into ApolloX.
    edges = [_edge(COFUNDER, PAYEE, 2_000_000, 1000), _edge(COFUNDER, PAYEE, 1_500_000, 3900),
             _edge(F07, PAYEE, 2_000_000, 7400)]
    links = patterns.shared_payees(edges, cluster={F07}, quiet={PAYEE})
    assert [(row["wallet"], row["via"]) for row in links] == [(COFUNDER, PAYEE)]
    assert links[0]["outsider_usd"] == 3_500_000 and links[0]["cluster_usd"] == 2_000_000


def test_a_busy_or_unmeasured_payee_links_nobody():
    edges = [_edge(COFUNDER, PAYEE, 2_000_000, 1000), _edge(F07, PAYEE, 2_000_000, 7400)]
    assert patterns.shared_payees(edges, cluster={F07}, quiet=set()) == []


def test_payments_weeks_apart_do_not_link():
    edges = [_edge(COFUNDER, PAYEE, 2_000_000, 1000), _edge(F07, PAYEE, 2_000_000, 1000 + 30 * DAY)]
    assert patterns.shared_payees(edges, cluster={F07}, quiet={PAYEE}) == []


def test_dust_does_not_link():
    edges = [_edge(COFUNDER, PAYEE, 0.5, 1000), _edge(F07, PAYEE, 2_000_000, 7400)]
    assert patterns.shared_payees(edges, cluster={F07}, quiet={PAYEE}) == []


# --- how much of HIS money reached an address ------------------------------------

def test_his_money_is_shared_out_by_what_each_sender_holds_of_it():
    x, y, z = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40
    edges = [_edge(F07, x, 100, 1), _edge("0x" + "9" * 40, x, 100, 2), _edge(x, y, 50, 3)]
    got = value.his_money(edges, seeds={F07}, boundaries=set())
    assert got[x]["in_usd"] == 100 and got[x]["share"] == 0.5
    assert got[y]["in_usd"] == 25 and got[y]["depth"] == 2
    assert z not in got


def test_a_boundary_passes_none_of_his_money_on():
    ex, after = "0x" + "e" * 40, "0x" + "4" * 40
    edges = [_edge(F07, ex, 1_000_000, 1), _edge(ex, after, 1_000_000, 2)]
    got = value.his_money(edges, seeds={F07}, boundaries={ex})
    assert got[ex]["in_usd"] == 1_000_000
    assert after not in got


def test_a_round_trip_back_to_him_cannot_inflate_anything():
    x = "0x" + "1" * 40
    edges = [_edge(F07, x, 100, 1), _edge(x, F07, 100, 2), _edge(F07, x, 100, 3)]
    got = value.his_money(edges, seeds={F07}, boundaries=set())
    assert got[x]["share"] == 1.0 and got[x]["in_usd"] == 200
    assert F07 not in got


def test_an_unvalued_transfer_still_reaches_the_address():
    x = "0x" + "1" * 40
    edges = [_edge(F07, x, None, 1)]
    got = value.his_money(edges, seeds={F07}, boundaries=set())
    assert got[x]["unvalued"] == 1 and got[x]["in_usd"] == 0


# --- L1 deposit addresses used for weeks ----------------------------------------
#
# chain.labels.infer_deposit_addresses anchors its 24h window from an address's
# FIRST inflow to its LARGEST forward. 0x841b9e4f (his, 2023) took deposits from
# 08-18 to 12-27, each swept to Binance 14 within 30 minutes, and its largest
# forward came seven days after its first inflow — so that rule calls it a wallet.

HOT = "0x28c6c06298d514db089934071355e5743bf21d60"
D841 = "0x841b9e4f5b5edf84e71da3aae2db8d4b3fb2eed4"


def _l1(src, dst, usd, ts):
    return {"src": src, "dst": dst, "amount_usd": usd, "ts": ts, "chain": "ethereum"}


def test_a_deposit_address_used_for_weeks_is_recognised_deposit_by_deposit():
    t = 1_692_360_000
    edges = [_l1(F07, D841, 3_945_000, t), _l1(D841, HOT, 3_950_000, t + 960),
             _l1(F07, D841, 8_008_498, t + 7 * DAY), _l1(D841, HOT, 8_008_498, t + 7 * DAY + 300),
             _l1(F07, D841, 6_000_000, t + 131 * DAY), _l1(D841, HOT, 6_000_000, t + 131 * DAY + 1600)]
    assert patterns.l1_deposit_hot(edges, D841, hot={HOT}) == HOT


def test_an_inflow_left_sitting_for_days_is_not_a_deposit_address():
    t = 1_692_360_000
    edges = [_l1(F07, D841, 1_000_000, t), _l1(D841, HOT, 1_000_000, t + 5 * DAY)]
    assert patterns.l1_deposit_hot(edges, D841, hot={HOT}) is None


def test_spending_elsewhere_is_not_a_deposit_address():
    t = 1_692_360_000
    edges = [_l1(F07, D841, 1_000_000, t), _l1(D841, HOT, 800_000, t + 600),
             _l1(D841, "0x" + "5" * 40, 200_000, t + 700)]
    assert patterns.l1_deposit_hot(edges, D841, hot={HOT}) is None


def test_a_payee_with_many_senders_is_not_single_purpose():
    # 0x160f6ef9 (production dry run): paid by his wallets AND by mints, Binance
    # hot wallets, WETH and a dozen others. Sharing it links nobody.
    edges = [_edge(F07, PAYEE, 2_000_000, 1000)]
    edges += [_edge("0x" + f"{i:040x}", PAYEE, 50_000, 1000 + i) for i in range(1, 9)]
    assert patterns.shared_payees(edges, cluster={F07}, quiet={PAYEE}) == []


def test_a_service_sender_is_never_linked():
    edges = [_edge(COFUNDER, PAYEE, 2_000_000, 1000), _edge(F07, PAYEE, 2_000_000, 7400)]
    assert patterns.shared_payees(edges, cluster={F07}, quiet={PAYEE},
                                  boundaries={COFUNDER}) == []
