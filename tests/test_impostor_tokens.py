# tests/test_impostor_tokens.py
"""A token is what its contract is, not what its ticker claims.

Transfers were priced from `tokenSymbol`, which the sender chooses. Anyone can
deploy a token called "USDC" for a few cents, and address-poisoning kits do
exactly that — one observed transaction carried 599 transfers of a token
symboled USDC from an unverified contract Arbiscan flags "poor reputation".
Every record already stored `token_address`; nothing compared it.

Measured across the whole substrate for the one pair with a canonical contract
verifiable from config: $4,480,222,225 counterfeit against $3,172,693,746
genuine. More than half the dollar figures in the system were fake.

The opposite error is worse than the bug: a wrong canonical entry quarantines
real money. So these tests pin that the check stays silent unless it KNOWS.
"""

import json

from scripts.quarantine_impostor_tokens import scrub_records
from src.chain.assets import is_impostor, load_canonical_contracts, value_usd

REAL = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
FAKE = "0xa38ae1eabad3b91441f65bcbbd3cb9fd9c58fb78"
CANON = {("arbitrum", "USDC"): REAL}


def test_the_real_contract_is_not_an_impostor():
    assert is_impostor("USDC", REAL, "arbitrum", CANON) is False


def test_a_different_contract_wearing_the_ticker_is_an_impostor():
    assert is_impostor("USDC", FAKE, "arbitrum", CANON) is True


def test_an_uncatalogued_pair_is_never_judged():
    """Without a canonical entry we cannot tell a counterfeit from a token we
    have not catalogued, and guessing discards real transfers."""
    assert is_impostor("USDC", FAKE, "ethereum", CANON) is False
    assert is_impostor("SOMETOKEN", FAKE, "arbitrum", CANON) is False


def test_a_missing_contract_is_never_judged():
    """Native transfers carry no contract, and legacy rows may lack one."""
    assert is_impostor("USDC", None, "arbitrum", CANON) is False
    assert is_impostor("USDC", REAL, None, CANON) is False
    assert is_impostor("USDC", REAL, "arbitrum", None) is False


def test_contract_comparison_is_case_insensitive():
    assert is_impostor("usdc", REAL.upper(), "ARBITRUM", CANON) is False


def test_value_usd_refuses_to_price_an_impostor():
    amount, basis = value_usd("USDC", 8_999_999.0, "2025-09-12", lambda s, d: None,
                              contract=FAKE, chain="arbitrum", canonical=CANON)
    assert amount is None
    assert basis == "impostor_token"


def test_value_usd_still_prices_the_real_token():
    amount, basis = value_usd("USDC", 100.0, "2025-09-12", lambda s, d: None,
                              contract=REAL, chain="arbitrum", canonical=CANON)
    assert amount == 100.0
    assert basis == "stable_par"


def test_value_usd_is_unchanged_without_a_canonical_map():
    """Existing callers that pass no contract must behave exactly as before."""
    amount, basis = value_usd("USDC", 100.0, "2025-09-12", lambda s, d: None)
    assert (amount, basis) == (100.0, "stable_par")


def test_load_canonical_contracts_seeds_from_config():
    canon = load_canonical_contracts({"usdc_contract_arbitrum": REAL.upper()})
    assert canon[("arbitrum", "USDC")] == REAL


def test_load_canonical_contracts_survives_a_missing_registry(tmp_path):
    canon = load_canonical_contracts({}, tmp_path / "nope.json")
    assert canon == {}


def test_load_canonical_contracts_reads_a_registry_file(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"tokens": [
        {"chain": "ethereum", "symbol": "usdt", "contract": "0xABC"}]}))
    canon = load_canonical_contracts({}, path)
    assert canon[("ethereum", "USDT")] == "0xabc"


def _rec(contract, usd=8_999_999.0, **over):
    rec = {"id": "arbitrum:0xtx:erc20:0", "chain": "arbitrum", "asset": "USDC",
           "token_address": contract, "amount_usd": usd,
           "value_basis": "stable_par", "spam": False, "spam_reason": None}
    rec.update(over)
    return rec


def test_scrub_marks_the_counterfeit_and_removes_its_value():
    records = [_rec(FAKE)]
    marked, removed = scrub_records(records, CANON)
    assert marked == 1
    assert removed == 8_999_999.0
    r = records[0]
    # None, never 0.0 — a forgery has no value, and 0.0 is a number thresholds
    # silently accept.
    assert r["amount_usd"] is None
    assert r["value_basis"] == "impostor_token"
    assert r["spam"] is True


def test_scrub_leaves_the_genuine_record_untouched():
    records = [_rec(REAL)]
    marked, removed = scrub_records(records, CANON)
    assert (marked, removed) == (0, 0.0)
    assert records[0]["amount_usd"] == 8_999_999.0
    assert records[0]["spam"] is False


def test_scrub_is_idempotent():
    records = [_rec(FAKE)]
    scrub_records(records, CANON)
    marked, removed = scrub_records(records, CANON)
    assert (marked, removed) == (0, 0.0)


def test_scrub_ignores_uncatalogued_tokens():
    records = [_rec(FAKE, asset="WEIRDCOIN")]
    assert scrub_records(records, CANON) == (0, 0.0)


def test_scrub_survives_junk_rows():
    records = ["not a dict", None, _rec(FAKE)]
    marked, _ = scrub_records(records, CANON)
    assert marked == 1
