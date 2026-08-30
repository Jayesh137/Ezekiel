# tests/test_chain_labels.py
import json

import pytest

from src import transfer_graph as tg
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


def test_service_addresses_categories_narrows_and_gates_inferred():
    """A caller asking a different question than transfer_graph's ("may I walk
    into this address?") passes a narrower category set. Linkage asks "does
    shared use imply common ownership?", for which a cex_deposit address is
    the strongest possible yes — so it must survive a category filter that
    omits cex_deposit, while a real service category is still caught.

    `inferred` entries are always cex_deposit, so they must be gated by the
    same filter — otherwise a caller that deliberately excluded that category
    would have it silently reintroduced through the back door.
    """
    deposit_registry = labels.load_registry_data({"entities": [
        {"address": "0xcexdeposit", "chain": "arbitrum", "entity": "Binance deposit",
         "category": "cex_deposit", "source": "curated", "added": "2026-08-28"},
        {"address": BINANCE, "chain": "ethereum", "entity": "Binance 8",
         "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    ]})
    narrow = labels.SERVICE_CATEGORIES - {"cex_deposit", "cex_deposit_sweep"}

    got = labels.service_addresses(deposit_registry, categories=narrow)
    assert BINANCE in got, "a real infrastructure category must still be caught"
    assert "0xcexdeposit" not in got, "a deposit address must not be treated as infrastructure"

    inferred = {"0xinferred": {"category": "cex_deposit"}}
    got_with_inferred = labels.service_addresses(deposit_registry, inferred, categories=narrow)
    assert "0xinferred" not in got_with_inferred, (
        "inferred entries are all cex_deposit and must respect the same category filter"
    )


def test_service_addresses_default_categories_is_unchanged():
    """transfer_graph.py calls service_addresses(registry) positionally, with
    no categories argument. That call site must keep behaving exactly as
    before — this is the regression guard for it."""
    inferred = {"0xdeposit": {"category": "cex_deposit", "entity": "Binance (inferred)"}}
    assert (labels.service_addresses(registry(), inferred)
            == labels.service_addresses(registry(), inferred, categories=None)
            == labels.service_addresses(registry(), inferred,
                                        categories=labels.SERVICE_CATEGORIES))


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


# --- the bytecode tier must actually reach the running system ------------------
#
# classify_address, CodeCache and fetch_code had zero production call sites, so
# the binding constraint "an address with bytecode is not a person and can never
# be graded MIGRATION_CANDIDATE" was asserted in prose and unenforced in code.

CONFIG = {"chains": [{"name": "arbitrum", "chain_id": 42161, "native": "ETH",
                      "enabled": True, "priority": 0},
                     {"name": "base", "chain_id": 8453, "native": "ETH",
                      "enabled": True, "priority": 1}]}
TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
CONTRACT = "0x" + "c" * 40
PERSON = "0x" + "e" * 40


class FakeCache:
    def __init__(self, codes):
        self.codes = codes
        self.asked = []

    def has_code(self, address, chain):
        self.asked.append((address, chain["name"]))
        return self.codes.get(address)


def _edge(dst, usd=500_000.0, chain="arbitrum", src=TARGET):
    """A real graph edge, via the same normaliser the substrate feeds."""
    return tg.normalise_transfer_record({
        "src": src, "dst": dst, "chain": chain, "amount_usd": usd,
        "asset": "USDC", "ts": 1781000000, "tx_hash": f"0x{dst[-4:]}{int(usd)}",
        "kind": "erc20", "spam": False})


def test_an_address_with_bytecode_becomes_a_known_service():
    known = set()
    cache = FakeCache({CONTRACT: True, PERSON: False})
    found = tg.label_contracts([_edge(CONTRACT), _edge(PERSON)], known, CONFIG, 1.0,
                               cache=cache)
    assert found == {CONTRACT: "arbitrum"}
    assert known == {CONTRACT}


def test_a_contract_can_never_be_graded_a_migration_candidate():
    """The constraint itself, end to end through the grader."""
    known = set()
    edges = [_edge(CONTRACT, usd=9_000_000.0)]
    tg.label_contracts(edges, known, CONFIG, 1.0, cache=FakeCache({CONTRACT: True}))

    graph = tg.build_graph(edges, TARGET, known_services=known,
                           behavioural={CONTRACT: 0.99})
    node = next(n for n in graph["nodes"] if n["wallet"] == CONTRACT)
    assert node["classification"] == tg.CLASS_SERVICE
    assert node["confidence"] == 0.0

    # Without the bytecode tier the same address is graded as a person.
    ungraded = tg.build_graph(edges, TARGET, behavioural={CONTRACT: 0.99})
    assert next(n for n in ungraded["nodes"]
                if n["wallet"] == CONTRACT)["classification"] != tg.CLASS_SERVICE


def test_a_failed_lookup_never_marks_an_address_codeless():
    """has_code returns None when the lookup failed. Absence of evidence is not
    evidence of an externally owned account."""
    known = set()
    found = tg.label_contracts([_edge(CONTRACT)], known, CONFIG, 1.0,
                               cache=FakeCache({CONTRACT: None}))
    assert found == {} and known == set()


def test_only_gradeable_addresses_are_checked():
    """Sub-dust poisoning clones make up most raw edges and can never be graded,
    and an address already known to be infrastructure needs no call."""
    known = {"0x" + "a" * 40}
    cache = FakeCache({})
    tg.label_contracts([
        _edge(CONTRACT, usd=0.4),               # sub-dust: never a node
        _edge("0x" + "a" * 40, usd=500_000.0),  # already a known service
        _edge(PERSON, usd=500_000.0),
    ], known, CONFIG, 1.0, cache=cache)
    assert [a for a, _c in cache.asked] == [PERSON]


def test_each_address_is_checked_on_the_chain_it_was_seen_on():
    """A contract at an address on one chain need not exist at the same address
    on another, so the question is only meaningful per chain."""
    cache = FakeCache({CONTRACT: True})
    tg.label_contracts([_edge(CONTRACT, usd=10.0, chain="arbitrum"),
                        _edge(CONTRACT, usd=900.0, chain="base")],
                       set(), CONFIG, 1.0, cache=cache)
    assert cache.asked == [(CONTRACT, "base")]      # its largest edge


def test_the_highest_value_addresses_are_checked_first():
    """If the per-run cap bites, the addresses closest to being graded are the
    ones that got checked."""
    cache = FakeCache({})
    tg.label_contracts([_edge("0x" + "1" * 40, usd=10.0),
                        _edge("0x" + "2" * 40, usd=900_000.0),
                        _edge("0x" + "3" * 40, usd=500.0)],
                       set(), CONFIG, 1.0, cache=cache)
    assert [a for a, _c in cache.asked] == ["0x" + "2" * 40, "0x" + "3" * 40,
                                            "0x" + "1" * 40]


def test_code_cache_asks_once_per_address_and_never_caches_a_failure(tmp_path):
    calls = []

    def fetcher(addr, chain):
        calls.append(addr)
        return None if addr == PERSON else "0x6080604052"

    from src.chain.labels import CodeCache
    cache = CodeCache(tmp_path / "code_cache.json", fetcher)
    chain = CONFIG["chains"][0]

    assert cache.has_code(CONTRACT, chain) is True
    assert cache.has_code(CONTRACT, chain) is True
    assert calls == [CONTRACT]                       # cached, one call ever

    assert cache.has_code(PERSON, chain) is None
    assert cache.has_code(PERSON, chain) is None
    assert calls == [CONTRACT, PERSON, PERSON]       # a failure is retried


def test_labelling_a_graph_without_enabled_chains_is_a_no_op():
    assert tg.label_contracts([_edge(CONTRACT)], set(), {"chains": []}, 1.0,
                              cache=FakeCache({CONTRACT: True})) == {}


def test_labelling_never_reaches_the_network_without_a_key(monkeypatch):
    """The real cache path, with fetch_code stubbed to prove the wiring: a
    budget-exhausted or keyless lookup degrades to unknown, not to codeless."""
    monkeypatch.setattr("src.chain.client.etherscan_get",
                        lambda *a, **k: pytest.fail("must not call the API"))
    monkeypatch.setattr("src.chain.client.fetch_code", lambda a, c, b: None)
    known = set()
    tg.label_contracts([_edge(CONTRACT)], known, CONFIG, 1.0)
    assert known == set()
