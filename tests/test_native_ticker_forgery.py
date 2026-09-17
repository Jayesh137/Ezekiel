# tests/test_native_ticker_forgery.py
"""A token is its contract (rule 2), including the chain's own native asset.

Measured 2026-09-16 across 674,088 stored records, still priced as real:

- 25 ERC-20 contracts calling themselves "ETH" on Ethereum and Arbitrum,
  ~$30.5M at ETH's daily close, each with a single sender. One "paid" $5.26M
  from `0xf078969e…` to `0x8579b784…0eb68e`, a vanity look-alike of his own
  Binance deposit address `0x8570c2ae…0eb68e`. Native ETH has no contract.
- 15 contracts calling themselves "USDT" on Polygon, $54.3M at par, because no
  registry row existed for the ticker there — `0x26c68e12…` (supply one
  trillion) alone $53.7M, paid by look-alikes of his real counterparties.
"""

import json

from src.chain import assets

REGISTRY = assets.load_canonical_contracts(
    {}, registry_path="data/labels/token_contracts.json")


def test_an_erc20_named_eth_is_an_impostor_where_eth_is_native():
    for chain in ("ethereum", "arbitrum", "base", "optimism"):
        assert assets.is_impostor("ETH", "0xfe33ec7536a55f2c69bbe98381e917f67017e60f",
                                  chain, REGISTRY), chain


def test_native_eth_itself_is_never_an_impostor():
    assert not assets.is_impostor("ETH", None, "ethereum", REGISTRY)
    assert not assets.is_impostor("ETH", "", "arbitrum", REGISTRY)


def test_optimisms_legacy_eth_token_is_genuine():
    assert not assets.is_impostor("ETH", "0xDeadDeAddeAddEAddeadDEaDDEAdDeaDDeAD0000",
                                  "optimism", REGISTRY)


def test_an_eth_token_on_a_chain_where_eth_is_not_native_is_left_alone():
    """Silent without a rule, as always: BSC's Binance-Peg ETH is genuine."""
    assert not assets.is_impostor("ETH", "0x2170ed0880ac9a755fd29b2688956bd959f933f8",
                                  "bsc", REGISTRY)


def test_a_forged_eth_is_priced_as_a_forgery_not_as_eth():
    usd, basis = assets.value_usd("ETH", 1500.0, "2026-08-01", lambda s, d: 2900.0,
                                  contract="0xfe33ec7536a55f2c69bbe98381e917f67017e60f",
                                  chain="ethereum", canonical=REGISTRY)
    assert (usd, basis) == (None, "impostor_token")
    usd, basis = assets.value_usd("ETH", 1.5, "2026-08-01", lambda s, d: 2900.0,
                                  contract=None, chain="ethereum", canonical=REGISTRY)
    assert basis == "daily_close" and usd == 4350.0


def test_polygon_usdt_from_anything_but_tether_is_an_impostor():
    assert assets.is_impostor("USDT", "0x26c68e1277202838d0dcab89330b033a7db5db76",
                              "polygon", REGISTRY)
    assert not assets.is_impostor("USDT", "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
                                  "polygon", REGISTRY)
    assert not assets.is_impostor("USDT0", "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
                                  "polygon", REGISTRY)


def test_loading_the_registry_never_mutates_the_module_defaults(tmp_path):
    before = {k: set(v) for k, v in assets.CANONICAL_CONTRACTS.items()}
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"tokens": [
        {"chain": "optimism", "symbol": "ETH", "contract": "0x" + "ab" * 20}]}))
    loaded = assets.load_canonical_contracts({}, registry_path=path)
    assert "0x" + "ab" * 20 in loaded[("optimism", "ETH")]
    assert assets.CANONICAL_CONTRACTS == before
