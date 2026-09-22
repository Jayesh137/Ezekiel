"""A measured exchange or contract is never a candidate.

The phone review app shows the roster's own tiers, so an exchange hot wallet or
a protocol contract that reaches POSSIBLE is one the operator is asked to judge
as "maybe him". Measured 2026-09-22 on the live roster: of 33 POSSIBLE leads,
FIVE were globally busy addresses and THREE were named contracts
(DeusdMerkleDistributor, BoringSolver, GnosisSafeProxy) — none of which can be
a wallet he trades from.

Fan degree inside our own substrate cannot catch either: an exchange the
cluster used twice never trips it (rule 9), and a contract's code is not a
fan-out pattern at all. Only the whole-chain reading answers, which is what
`chain/activity.py` exists for.
"""
import pytest

from src import roster


def test_a_busy_address_is_graded_a_service():
    table = {"arbitrum:0xaaa": {"txs": 2_282_986, "token_transfers": 10, "is_contract": False}}
    out = roster.services_from_activity(table, ground_truth=set())
    assert "0xaaa" in out
    assert "2,282,986" in out["0xaaa"]


def test_a_contract_is_graded_a_service_even_when_it_is_quiet():
    # 35 transactions: nothing about the VOLUME says service. The code does.
    table = {"arbitrum:0xbbb": {"txs": 35, "token_transfers": 3026,
                                "is_contract": True, "name": "GnosisSafeProxy"}}
    out = roster.services_from_activity(table, ground_truth=set())
    assert "0xbbb" in out
    assert "GnosisSafeProxy" in out["0xbbb"]


def test_an_unmeasured_address_is_not_a_service():
    """Rule 9, pointed the other way: unmeasured is not evidence of a person,
    and it is not evidence of a service either. It stays a candidate."""
    assert roster.services_from_activity({}, ground_truth=set()) == {}


def test_a_quiet_eoa_is_left_alone():
    table = {"arbitrum:0xccc": {"txs": 24, "token_transfers": 9, "is_contract": False}}
    assert roster.services_from_activity(table, ground_truth=set()) == {}


def test_config_ground_truth_is_immune():
    """A wallet the operator named is ground truth and outranks a measurement,
    the same rule spam.ground_truth_addresses already applies."""
    table = {"arbitrum:0xddd": {"txs": 9_000_000, "token_transfers": 0, "is_contract": True}}
    assert roster.services_from_activity(table, ground_truth={"0xddd"}) == {}


def test_one_chain_busy_is_enough():
    """An address quiet on one chain and an exchange on another is an exchange."""
    table = {"arbitrum:0xeee": {"txs": 3, "token_transfers": 0, "is_contract": False},
             "ethereum:0xeee": {"txs": 1_400_000, "token_transfers": 5, "is_contract": False}}
    assert "0xeee" in roster.services_from_activity(table, ground_truth=set())


def test_a_malformed_reading_is_ignored_rather_than_guessed():
    table = {"arbitrum:0xfff": None, "bad-key": {"txs": 5_000_000}}
    assert roster.services_from_activity(table, ground_truth=set()) == {}


@pytest.mark.parametrize("tier", ["CONFIRMED", "PROBABLE", "POSSIBLE", "WATCH"])
def test_a_service_verdict_overrides_every_tier(tier):
    """assign_tier already does this; pinned here because the whole guard rests
    on it, and the vectors that reach a contract are real transfers."""
    assert roster.assign_tier({"transfer", "linkage"}, 0.9, True, False) == roster.TIER_INFRASTRUCTURE


def test_a_contract_flag_does_not_bury_a_live_hyperliquid_account():
    """Measured 2026-09-22: `0xb798aef7…` is an EOA on Arbitrum (108 txs) and a
    CONTRACT on Ethereum (649 txs), and it runs a live HL account holding $9.7M
    with 2,000 fills. The first version of this guard graded it infrastructure
    and removed the one thing the mission is about — an address that trades on
    Hyperliquid. Code on some other chain does not unmake a trading account."""
    table = {"ethereum:0xb79": {"txs": 649, "token_transfers": 2685, "is_contract": True},
             "arbitrum:0xb79": {"txs": 108, "token_transfers": 933, "is_contract": False}}
    assert "0xb79" in roster.services_from_activity(table, ground_truth=set())
    assert roster.services_from_activity(table, ground_truth=set(),
                                         hl_accounts={"0xb79"}) == {}


def test_an_exchange_stays_an_exchange_even_with_an_hl_account():
    """The exemption is for the CONTRACT verdict only. Exchange-scale volume is
    a statement about what the address is used for, and a hot wallet with an HL
    account is still a hot wallet."""
    table = {"arbitrum:0xhot": {"txs": 2_282_986, "token_transfers": 10, "is_contract": False}}
    assert "0xhot" in roster.services_from_activity(table, ground_truth=set(),
                                                    hl_accounts={"0xhot"})
