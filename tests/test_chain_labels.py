# tests/test_chain_labels.py
import json

from src.chain import labels

BINANCE = "0xf977814e90da44bfa03b6295a0616a897441acec"
BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"


def registry():
    return labels.load_registry_data({"entities": [
        {"address": BINANCE, "chain": "ethereum", "entity": "Binance 8",
         "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
        {"address": BRIDGE, "chain": "arbitrum", "entity": "Hyperliquid Bridge2",
         "category": "hl_infra", "source": "config", "added": "2026-08-28"},
    ]})


def test_the_shipped_registry_parses_and_uses_only_known_categories():
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "labels" / "entities.json"
    data = json.loads(path.read_text())
    for entry in data["entities"]:
        assert entry["category"] in labels.SERVICE_CATEGORIES, entry
        assert entry["address"] == entry["address"].lower(), entry
        assert entry["source"], entry


def test_a_curated_label_wins_over_every_other_signal():
    got = labels.classify_address(BINANCE, registry(), has_code=False,
                                  fan_reason="high fan-in (900 senders)",
                                  inferred={"category": "cex_deposit"})
    assert got["category"] == "cex_hot"
    assert got["entity"] == "Binance 8"
    assert got["source"] == "curated"
    assert got["is_service"] is True


def test_an_inferred_label_beats_fan_degree():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=False,
                                  fan_reason="high fan-in (900 senders)",
                                  inferred={"category": "cex_deposit",
                                            "entity": "Binance (inferred deposit)"})
    assert got["category"] == "cex_deposit"
    assert got["source"] == "inferred"


def test_bytecode_makes_an_address_a_contract_and_never_a_person():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=True)
    assert got["category"] == "contract"
    assert got["source"] == "code"
    assert got["is_service"] is True


def test_fan_degree_is_the_last_resort():
    got = labels.classify_address("0xdeadbeef", registry(), has_code=False,
                                  fan_reason="many-to-many flow (40 senders, 40 recipients)")
    assert got["category"] == "service"
    assert got["source"] == "fan_degree"
    assert got["is_service"] is True


def test_an_ordinary_wallet_is_not_a_service():
    got = labels.classify_address("0xa95d9c1f655341597c94393fddc30cf3c08e4fce",
                                  registry(), has_code=False)
    assert got["category"] is None
    assert got["source"] is None
    assert got["is_service"] is False


def test_unknown_bytecode_state_does_not_invent_a_contract():
    """has_code=None means we could not read it. Absence of evidence is not
    evidence of a contract."""
    got = labels.classify_address("0xdeadbeef", registry(), has_code=None)
    assert got["category"] is None
    assert got["is_service"] is False


def test_a_deposit_address_forwarding_almost_everything_to_a_cex_is_inferred():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xdeposit", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xdeposit", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 2 * hour},
    ]
    got = labels.infer_deposit_addresses(records, {BINANCE})
    assert "0xdeposit" in got
    assert got["0xdeposit"]["category"] == "cex_deposit"
    assert got["0xdeposit"]["forwarded_to"] == BINANCE


def test_forwarding_too_little_is_not_a_deposit_address():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 500_000.0, "ts": 2 * hour},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_forwarding_too_late_is_not_a_deposit_address():
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 40 * 3600},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_a_wallet_with_other_material_destinations_is_not_a_deposit_address():
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 950_000.0, "ts": hour},
        {"src": "0xmaybe", "dst": "0xelsewhere", "amount_usd": 300_000.0, "ts": hour},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_material_destinations_fanned_across_many_small_transfers_are_not_a_deposit_address():
    """No single sibling destination clears the 5% bar on its own, but ten of
    them together move 49% of everything received. The guard has to look at
    the total sent elsewhere, not just the largest single destination."""
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 950_000.0, "ts": hour},
    ] + [
        {"src": "0xmaybe", "dst": f"0xother{i}", "amount_usd": 49_000.0, "ts": hour}
        for i in range(10)
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_an_incidental_small_send_to_a_hot_wallet_does_not_anchor_the_forwarding_window():
    """An early $1 test-send to the hot wallet must not make a bulk forward
    100 hours later look like it happened "quickly"."""
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 1.0, "ts": hour},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 100 * hour},
    ]
    assert labels.infer_deposit_addresses(records, {BINANCE}) == {}


def test_a_fast_bulk_forward_is_inferred_using_its_own_timing_not_an_incidental_earlier_send():
    """The real forward is still caught, and the reported timing belongs to
    the transfer that actually carried the value, not the earliest one."""
    hour = 3600
    records = [
        {"src": "0xcluster", "dst": "0xmaybe", "amount_usd": 1_000_000.0, "ts": 0},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 1.0, "ts": hour},
        {"src": "0xmaybe", "dst": BINANCE, "amount_usd": 999_000.0, "ts": 2 * hour},
    ]
    got = labels.infer_deposit_addresses(records, {BINANCE})
    assert "0xmaybe" in got
    assert got["0xmaybe"]["forwarded_to"] == BINANCE
    assert got["0xmaybe"]["hours_to_forward"] == 2.0


def test_service_addresses_unions_curated_and_inferred():
    inferred = {"0xdeposit": {"category": "cex_deposit", "entity": "Binance (inferred)"}}
    got = labels.service_addresses(registry(), inferred)
    assert BINANCE in got and BRIDGE in got and "0xdeposit" in got


def test_code_cache_asks_once_and_persists(tmp_path):
    calls = []

    def fetcher(address, chain):
        calls.append(address)
        return "0x60806040"

    cache = labels.CodeCache(tmp_path / "code_cache.json", fetcher)
    assert cache.has_code("0xABC", {"name": "arbitrum"}) is True
    assert cache.has_code("0xabc", {"name": "arbitrum"}) is True
    assert calls == ["0xABC"]

    reloaded = labels.CodeCache(tmp_path / "code_cache.json", fetcher)
    assert reloaded.has_code("0xabc", {"name": "arbitrum"}) is True
    assert len(calls) == 1


def test_code_cache_returns_none_and_caches_nothing_on_a_read_failure(tmp_path):
    cache = labels.CodeCache(tmp_path / "code_cache.json", lambda a, c: None)
    assert cache.has_code("0xabc", {"name": "arbitrum"}) is None
    assert not (tmp_path / "code_cache.json").exists()
