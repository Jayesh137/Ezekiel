import json

from src.chain import assets


def test_stablecoins_value_at_par_without_consulting_a_price_source():
    def never_called(symbol, date):
        raise AssertionError("stables must not need a price lookup")

    amount, basis = assets.value_usd("USDC", 5_000_000.0, "2026-06-16", never_called)
    assert amount == 5_000_000.0
    assert basis == "stable_par"


def test_stablecoin_matching_is_case_and_variant_insensitive():
    for symbol in ("usdc", "USDC.e", "USDT", "DAI", "USDe"):
        _, basis = assets.value_usd(symbol, 1.0, "2026-06-16", lambda s, d: None)
        assert basis == "stable_par", symbol


def test_majors_use_the_daily_close():
    amount, basis = assets.value_usd("WETH", 3.0, "2026-06-16", lambda s, d: 2000.0)
    assert amount == 6000.0
    assert basis == "daily_close"


def test_a_missing_price_yields_none_and_never_zero():
    """A $2M ETH transfer booked as 0.0 drops below every threshold in the
    system and the migration walks past unnoticed."""
    amount, basis = assets.value_usd("WETH", 1000.0, "2026-06-16", lambda s, d: None)
    assert amount is None
    assert basis == "unpriced"
    assert amount != 0.0        # the distinction this whole rule exists for


def test_an_unknown_token_is_unpriced_not_valued():
    amount, basis = assets.value_usd("SCAMAIRDROP", 1e9, "2026-06-16", lambda s, d: 1.0)
    assert amount is None
    assert basis == "unpriced"


def test_decimals_come_from_the_row_for_erc20_and_are_18_for_native():
    assert assets.decimals_of({"tokenDecimal": "6"}, "erc20") == 6
    assert assets.decimals_of({}, "native") == 18
    assert assets.decimals_of({}, "internal") == 18
    assert assets.decimals_of({"tokenDecimal": "bogus"}, "erc20") == 18


def test_price_cache_reads_and_writes_disk_and_only_fetches_once(tmp_path):
    calls = []

    def fetch(symbol, date):
        calls.append((symbol, date))
        return 2500.0

    cache = assets.PriceCache(tmp_path, fetch=fetch)
    assert cache.get("WETH", "2026-06-16") == 2500.0
    assert cache.get("WETH", "2026-06-16") == 2500.0
    assert calls == [("WETH", "2026-06-16")]

    reloaded = assets.PriceCache(tmp_path, fetch=fetch)
    assert reloaded.get("WETH", "2026-06-16") == 2500.0
    assert calls == [("WETH", "2026-06-16")]
    assert json.loads((tmp_path / "WETH.json").read_text())["2026-06-16"] == 2500.0


def test_price_cache_records_a_miss_so_it_is_not_retried_every_run(tmp_path):
    calls = []

    def fetch(symbol, date):
        calls.append((symbol, date))
        return None

    cache = assets.PriceCache(tmp_path, fetch=fetch)
    assert cache.get("WETH", "2026-06-16") is None
    assert cache.get("WETH", "2026-06-16") is None
    assert len(calls) == 1
