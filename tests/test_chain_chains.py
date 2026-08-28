import pytest

from src.chain import chains


def test_defaults_include_the_six_phase_one_chains():
    names = {c["name"] for c in chains.DEFAULT_CHAINS}
    assert names == {"arbitrum", "ethereum", "base", "optimism", "polygon", "bsc"}
    assert {c["name"]: c["chain_id"] for c in chains.DEFAULT_CHAINS}["arbitrum"] == 42161


def test_enabled_chains_falls_back_to_defaults_when_config_is_silent():
    got = chains.enabled_chains({})
    assert [c["name"] for c in got] == [c["name"] for c in chains.DEFAULT_CHAINS]


def test_enabled_chains_filters_disabled_and_sorts_by_priority():
    cfg = {"chains": [
        {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 9},
        {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0},
        {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": False, "priority": 1},
    ]}
    assert [c["name"] for c in chains.enabled_chains(cfg)] == ["arbitrum", "base"]


def test_chain_by_name_is_case_insensitive_and_raises_on_unknown():
    cfg = {"chains": list(chains.DEFAULT_CHAINS)}
    assert chains.chain_by_name("ARBITRUM", cfg)["chain_id"] == 42161
    with pytest.raises(KeyError):
        chains.chain_by_name("solana", cfg)


def test_a_chain_entry_missing_required_keys_is_rejected_loudly():
    with pytest.raises(ValueError):
        chains.enabled_chains({"chains": [{"name": "base"}]})
