# tests/test_service_verification.py
"""Fan degree inside our substrate may not overrule whole-chain measurement.

Measured 2026-09-10: a personal DeFi wallet (an EOA with 35 Arbitrum
transactions) was graded a service on 84 senders / 130 recipients that were
mostly protocols, so the wallet sharing the target's real Binance deposit
address scored 0.0. Meanwhile SocketGateway, with 2.19M transactions, looked
like a five-sender private deposit address to the linkage vector.
"""

from src import linkage as lk
from src import transfer_graph as tg

T = "0x" + "1" * 40
HUB = "0x" + "2" * 40
AAVE = "0x" + "a" * 40
CCTP = "0x" + "c" * 40


def _edge(src, dst, usd=1000.0, ref=None, chain="arbitrum"):
    ref = ref or f"{src[:6]}-{dst[:6]}-{usd}"
    return {"id": tg.edge_id(src, dst, chain, ref), "src": src, "dst": dst,
            "chain": chain, "asset": "USDC", "amount_usd": usd, "ref": ref,
            "ts": 1, "timestamp": None, "discovery_source": tg.SRC_L1}


def _wallets(n, prefix):
    return [f"0x{prefix}{i:038x}" for i in range(n)]


def test_known_service_counterparties_do_not_count_toward_fan_degree():
    """A wallet using thirty protocols is not a hub."""
    protocols = _wallets(30, "9")
    edges = [_edge(HUB, p) for p in protocols] + [_edge(p, HUB) for p in protocols]
    plain = tg.detect_services(edges, set(), fanout=25, fanin=25)
    assert HUB in plain                       # without labels it looks like a hub
    labelled = tg.detect_services(edges, set(protocols), fanout=25, fanin=25)
    assert HUB not in labelled                # once they are named, it is a person


def test_a_measured_quiet_eoa_is_never_a_service_by_fan_degree():
    senders = _wallets(60, "5")
    edges = [_edge(s, HUB) for s in senders]
    assert HUB in tg.detect_services(edges, set(), fanout=25, fanin=25)
    assert HUB not in tg.detect_services(edges, set(), fanout=25, fanin=25,
                                         never_services={HUB})


def test_a_configured_service_is_still_reported_even_if_quiet():
    edges = [_edge(T, HUB)]
    svc = tg.detect_services(edges, {HUB}, never_services={HUB})
    assert HUB in svc


def test_fan_verdicts_split_busy_from_people_and_keep_the_unmeasured():
    fan = {"0xrouter": "many-to-many", "0xperson": "many-to-many",
           "0xunknown": "high fan-in", "0xcontract": "high fan-out"}
    readings = {
        "0xrouter": {"is_contract": True, "txs": 2_190_933, "token_transfers": 3_011_170},
        "0xperson": {"is_contract": False, "txs": 35, "token_transfers": 558},
        "0xunknown": None,
        # A quiet contract is neither a person nor confirmed busy: keeps the
        # fan verdict for traversal, and bytecode labelling handles the rest.
        "0xcontract": {"is_contract": True, "txs": 12, "token_transfers": 40},
    }
    busy, persons = tg.fan_verdicts(fan, readings)
    assert set(busy) == {"0xrouter"}
    assert "2,190,933 txs" in busy["0xrouter"]
    assert persons == {"0xperson"}


def test_verify_fan_services_asks_on_the_chain_the_value_moved_on(monkeypatch):
    asked = []

    class FakeCache:
        def get(self, addr, chain):
            asked.append((addr, chain))
            return {"is_contract": False, "txs": 3, "token_transfers": 3}

    senders = _wallets(60, "5")
    edges = [_edge(s, HUB, chain="ethereum") for s in senders] + [_edge(T, HUB, usd=1.0)]
    monkeypatch.setattr(tg, "persons_on_record", set)
    monkeypatch.setattr(tg, "load_config",
                        lambda: {"chains": [{"name": "arbitrum", "chain_id": 42161,
                                             "native": "ETH", "enabled": True, "priority": 0},
                                            {"name": "ethereum", "chain_id": 1,
                                             "native": "ETH", "enabled": True, "priority": 1}]})
    busy, persons = tg.verify_fan_services(
        edges, set(), {"service_fanout": 25, "service_fanin": 25}, cache=FakeCache())
    assert asked == [(HUB, "ethereum")]
    assert busy == {} and persons == {HUB}


# --- linkage: shared destinations must be measured, not assumed ----------------

class _Cache:
    def __init__(self, table):
        self.table = table

    def get(self, addr, chain):
        return self.table.get(addr)


def test_busy_and_unmeasured_destinations_cannot_be_shared_deposit_evidence():
    quiet = {"is_contract": False, "txs": 20, "token_transfers": 60}
    busy = {"is_contract": True, "txs": 2_000_000, "token_transfers": 3_000_000}
    cache = _Cache({"0xdeposit": quiet, "0xsocket": busy})
    excluded, pending = lk.activity_exclusions(
        ["0xdeposit", "0xsocket", "0xnew"], {"0xsocket": "arbitrum"}, cache)
    assert excluded == {"0xsocket", "0xnew"}
    assert pending == ["0xnew"]
    link = lk.compute_linkage("0xcand", None, {"0xdeposit", "0xsocket", "0xnew"},
                              T, None, {"0xdeposit", "0xsocket", "0xnew"}, excluded)
    assert link["shared_deposit_addresses"] == ["0xdeposit"]


def test_no_cache_means_every_destination_is_pending():
    excluded, pending = lk.activity_exclusions(["0xa", "0xb"], {}, None)
    assert excluded == {"0xa", "0xb"} and pending == ["0xa", "0xb"]
