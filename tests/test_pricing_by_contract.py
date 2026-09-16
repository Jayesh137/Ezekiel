# tests/test_pricing_by_contract.py
"""A token is its contract, not its ticker — in the direction that ACCEPTS.

Rule 2 has always been enforced one way here: `is_impostor` consults
`data/labels/token_contracts.json` to REJECT a token wearing a stablecoin's
ticker from the wrong contract. The opposite case had no path at all. A genuine
contract whose reported ticker does not match the registry fell through
`value_usd` to `(None, "unpriced")`, `classify_spam` called it
`unpriced_token`, and `sweep_wallet` dropped it — quarantined records never
reach `data/transfers/`, and the cursor advances past them, so the loss is
permanent.

Measured on the live quarantine ledger 2026-09-16: **332,636 records dropped as
`unpriced_token`**, of which 61.2% are three contracts verified on Blockscout as
genuine — Tether USDT0 (4,352,450 holders), Axelar Bridged USDC, and Aave v3
USDC. That is **33.2% of all 613,578 suppressions**, and only 5.2% of the
bucket carries an advertising-shaped symbol, which is the actual junk.

**The largest single case is one character.** Etherscan reports Tether's symbol
as `USD₮0` — U+20AE TUGRIK SIGN, not ASCII `T`. The registry entry `USDT0` is
CORRECT; `.upper()` does not fold `₮`, so it never matched, and 97,662 records
of real Tether were dropped by a glyph.

Two fixes, deliberately separate:

  * **normalise the symbol** before the registry lookup, so a homoglyph ticker
    reaches the entry that already exists;
  * **price by contract** when the registry knows it, so a genuine token is
    valued on the identity rule 2 already insists on rather than on the string
    it reports.

The second must never widen what gets priced at par on the strength of a
ticker. A contract is accepted only when `token_contracts.json` carries a
verified entry for it — the same registry, the same evidence bar, whose own
comment requires an on-chain or explorer verification per row. Anything else
is still `unpriced`, because pricing an unknown token at par is how $3.07B of
counterfeit value was booked as real.
"""

from src.chain import assets

ARB_USDT0 = "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"
UNKNOWN = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

AAVE_USDC = "0x724dc807b04555b71ed48a6896b6f41593b8c637"

# The registry shape load_canonical_contracts produces: (chain, SYMBOL) -> contract
CANONICAL = {("arbitrum", "USDT0"): ARB_USDT0, ("arbitrum", "AUSDC"): AAVE_USDC}


def _no_price(symbol, date_str):
    return None


# --- symbol normalisation --------------------------------------------------

def test_the_tugrik_ticker_folds_to_the_ascii_one():
    """U+20AE is what Etherscan actually returns for Tether."""
    assert assets.normalise_symbol("USD₮0") == "USDT0"


def test_normalisation_leaves_an_ordinary_ticker_alone():
    assert assets.normalise_symbol("usdc") == "USDC"
    assert assets.normalise_symbol("  ETH ") == "ETH"


def test_a_homoglyph_stablecoin_ticker_is_priced_at_par():
    """The 97,662-record case, end to end."""
    usd, basis = assets.value_usd("USD₮0", 1000.0, "2026-09-10", _no_price,
                                  contract=ARB_USDT0, chain="arbitrum",
                                  canonical=CANONICAL)

    assert (usd, basis) == (1000.0, "stable_par")


# --- pricing by contract ---------------------------------------------------

def test_a_registry_verified_contract_is_priced_even_with_an_odd_ticker():
    """The identity that decides is the contract, which rule 2 already says."""
    usd, basis = assets.value_usd("WHATEVER", 250.0, "2026-09-10", _no_price,
                                  contract=ARB_USDT0, chain="arbitrum",
                                  canonical=CANONICAL)

    assert usd == 250.0
    assert basis == "stable_par"


def test_an_unknown_contract_claiming_a_stable_ticker_is_still_refused():
    """Rule 2's original direction must not be weakened by its new one.

    A token merely CALLED USDT0 from a contract the registry does not know is
    an impostor, and $3.07B of counterfeit value says so.
    """
    usd, basis = assets.value_usd("USDT0", 1000.0, "2026-09-10", _no_price,
                                  contract=UNKNOWN, chain="arbitrum",
                                  canonical=CANONICAL)

    assert usd is None
    assert basis == "impostor_token"


def test_an_unknown_token_is_still_unpriced_never_par():
    """The registry is the only gate. Nothing is priced for looking plausible."""
    usd, basis = assets.value_usd("SOMETOKEN", 1000.0, "2026-09-10", _no_price,
                                  contract=UNKNOWN, chain="arbitrum",
                                  canonical=CANONICAL)

    assert usd is None
    assert basis == "unpriced"


def test_a_major_that_cannot_be_priced_stays_distinguishable():
    """`price_unavailable` is not `unpriced`, and classify_spam depends on it."""
    usd, basis = assets.value_usd("ETH", 1.0, "2026-09-10", _no_price)

    assert usd is None
    assert basis == "price_unavailable"


# --- the registry rows this rests on --------------------------------------

def test_the_verified_contracts_are_in_the_registry():
    """Each row was read on-chain in this session; see its `verified` field.

    A wrong entry here quarantines real money or prices a forgery at par, so
    the registry's own comment requires per-row verification rather than a
    plausible-looking address.
    """
    import json
    from pathlib import Path

    rows = json.loads(
        (Path(__file__).resolve().parents[1]
         / "data" / "labels" / "token_contracts.json").read_text())["tokens"]
    by_contract = {r.get("contract", "").lower(): r for r in rows}

    for contract in (ARB_USDT0,
                     "0xeb466342c4d449bc9f53a865d5cb90586f405215",   # Axelar USDC
                     "0x724dc807b04555b71ed48a6896b6f41593b8c637",   # Aave v3 USDC
                     "0x40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f",   # GHO
                     "0x6c3ea9036406852006290770bedfcaba0e23a0e8"):  # PYUSD
        assert contract in by_contract, contract
        assert by_contract[contract].get("verified"), contract


# --- par is a property of the CONTRACT, never of the ticker ----------------

def test_a_par_contract_is_valued_at_par_without_its_ticker_being_known():
    """`aArbUSDCn` is in no ticker registry and never will be.

    This is the whole reason par is keyed on the contract: Aave's aToken
    reports a symbol nothing could enumerate, while its contract is catalogued
    and verified.
    """
    usd, basis = assets.value_usd("aArbUSDCn", 5000.0, "2026-09-10", _no_price,
                                  contract=AAVE_USDC, chain="arbitrum",
                                  canonical=CANONICAL,
                                  par_contracts={AAVE_USDC})

    assert (usd, basis) == (5000.0, "stable_par")


def test_a_counterfeit_borrowing_a_par_tokens_ticker_is_not_priced():
    """The hole that adding these symbols to STABLES would have opened.

    A token calling itself GHO from a contract nobody verified must stay
    unpriced. Widening the ticker list would have priced it at par on every
    chain that has no registry row — rule 2's own failure mode, reintroduced
    by the fix for rule 2's opposite direction.
    """
    usd, basis = assets.value_usd("GHO", 1_000_000.0, "2026-09-10", _no_price,
                                  contract=UNKNOWN, chain="arbitrum",
                                  canonical=CANONICAL,
                                  par_contracts={AAVE_USDC})

    assert usd is None
    assert basis == "unpriced"


def test_par_contracts_are_loaded_from_the_registry():
    from pathlib import Path

    par = assets.load_par_contracts(
        Path(__file__).resolve().parents[1]
        / "data" / "labels" / "token_contracts.json")

    assert AAVE_USDC in par
    assert "0x6c3ea9036406852006290770bedfcaba0e23a0e8" in par   # PYUSD


def test_only_rows_declaring_par_are_par(tmp_path):
    """A registry row is not par just for existing — USDC's rows are not."""
    import json

    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"tokens": [
        {"chain": "arbitrum", "symbol": "AUSDC", "contract": AAVE_USDC, "par": True},
        {"chain": "ethereum", "symbol": "USDT", "contract": UNKNOWN},
    ]}))

    assert assets.load_par_contracts(p) == {AAVE_USDC}


def test_the_sweep_threads_par_contracts_into_valuation(monkeypatch):
    """Injected like `canonical`, for the reason collect.py's docstring gives.

    Reading the registry inside the sweep would make a test's result depend on
    the repository it runs in; and a guard the sweep never hands over is worth
    nothing, which is what the 796-record lookalike case already showed.
    """
    seen = {}

    def spy(symbol, amount, date_str, price_lookup, **kwargs):
        seen.update(kwargs)
        return 1.0, "stable_par"

    from src.chain import collect
    monkeypatch.setattr(collect, "value_usd", spy)

    collect.normalise_row(
        {"from": "0x1", "to": "0x2", "value": "1", "timeStamp": "1",
         "blockNumber": "1", "hash": "0xh", "tokenSymbol": "aArbUSDCn",
         "contractAddress": AAVE_USDC, "tokenDecimal": "6"},
        {"name": "arbitrum", "chain_id": 42161, "native": "ETH"},
        "erc20", lambda s, d: None,
        canonical=CANONICAL, par_contracts={AAVE_USDC})

    assert seen.get("par_contracts") == {AAVE_USDC}
