"""An address with no key is never a suspect (spec 2026-10-08 §9)."""

import json

import pytest

from src import not_wallets

T = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
EOA = "0x" + "ab" * 20
CONFIG = {"target_wallet": T, "known_self_wallets": [SELF],
          "excluded_addresses": ["0x" + "0" * 40, "0x2000000000000000000000000000000000000000"],
          "known_service_addresses": ["0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"]}


@pytest.mark.parametrize("address, needle", [
    ("0x" + "0" * 40, "zero"),
    ("0x0000000000000000000000000000000000001010", "precompile"),
    ("0x0000000000000000000000000000000000000800", "precompile"),
    ("0x" + "f" * 40, "all-ones"),
    ("0x2000000000000000000000000000000000000000", "system"),
    ("0x200000000000000000000000000000000000010c", "system"),
    ("0x" + "2" * 40, "hype"),
    ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "usdc (base)"),
    ("0x2791BCA1F2DE4661ED88A30C99A7A9449AA84174", "usdc.e (polygon)"),
    ("0x2df1c51e09aecf9cacb7bc98cb1742757f163df7", "configured"),
])
def test_keyless_addresses_are_not_wallets(address, needle):
    reason = not_wallets.classify(address, config=CONFIG)
    assert reason and needle in reason.lower()


def test_an_ordinary_address_is_a_wallet():
    assert not_wallets.classify(EOA, config=CONFIG) is None
    # 0x20 followed by ordinary bytes is an ordinary address, not the token range.
    assert not_wallets.classify("0x20" + "ab" * 19, config=CONFIG) is None
    assert not_wallets.classify(EOA) is None


def test_ground_truth_is_immune_even_when_listed():
    config = {**CONFIG, "known_service_addresses": [SELF, T]}
    assert not_wallets.classify(SELF, config=config) is None
    assert not_wallets.classify(T, config=config) is None


def test_malformed_input_is_not_an_address():
    assert not_wallets.classify("0x123", config=CONFIG) == "not an address"
    assert not_wallets.classify(None, config=CONFIG) == "not an address"


def test_the_pricing_registry_is_read_by_contract_not_comment(tmp_path):
    registry = tmp_path / "token_contracts.json"
    stranger = "0x" + "cd" * 20
    registry.write_text(json.dumps({
        "_comment": [f"measured on wallet {EOA}, which is NOT a token"],
        "tokens": [{"chain": "base", "symbol": "XYZ", "contract": stranger.upper().replace("0X", "0x")}]}))
    tokens = not_wallets.token_registry_addresses(registry)
    assert tokens == {stranger}
    assert "pricing registry" in not_wallets.classify(stranger, config=CONFIG, token_contracts=tokens)
    assert not_wallets.classify(EOA, config=CONFIG, token_contracts=tokens) is None


def test_an_unreadable_registry_contributes_nothing(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    assert not_wallets.token_registry_addresses(tmp_path / "bad.json") == set()
    assert not_wallets.token_registry_addresses(tmp_path / "absent.json") == set()


def test_discovery_never_selects_configured_exclusions():
    from src.market_discovery import exclusions
    got = {a.lower() for a in exclusions(CONFIG)}
    assert "0x" + "0" * 40 in got
    assert "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7" in got
