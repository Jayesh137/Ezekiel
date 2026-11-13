"""The transfer graph's in-memory edges stay under CPython's dict-size cliff.

Measured 2026-11-13: the trace runner's graph step held 6.23M edges and peaked
at 6.8 GB of 16. A dict's table is sized by key count, and in CPython 3.12 the
eleventh key takes it from 272 to 464 bytes; every substrate edge carried
eleven, one of them the ISO `timestamp` rendered from `ts`. And `dedupe_edges`
copied every edge while the caller's list was alive, so the build held two
copies of all of them. These pin both.
"""

import sys

from src import transfer_graph as tg

A = "0x" + "a" * 40
B = "0x" + "b" * 40


def _constructed_edges():
    record = {"src": A, "dst": B, "chain": "ethereum", "asset": "USDC", "amount_usd": 10.0,
              "tx_hash": "0x" + "1" * 64, "ts": 1_790_000_000, "kind": "erc20",
              "value_basis": "priced"}
    l1 = {"from": A, "to": B, "value": "1000000", "hash": "0x" + "2" * 64,
          "timeStamp": "1790000000", "tokenSymbol": "USDC", "tokenDecimal": "6"}
    send = {"time": 1_790_000_000_000, "hash": "0x" + "3" * 64,
            "delta": {"type": "send", "user": A, "destination": B, "token": "USDC", "usdcValue": "5"}}
    deposit = {"time": 1_790_000_000_000, "hash": "0x" + "4" * 64,
               "delta": {"type": "deposit", "usdc": "5"}}
    edges = {
        "substrate": tg.normalise_transfer_record(record),
        "l1": tg.normalise_l1_transfer(l1),
        "hl send": tg.normalise_hl_ledger_entry(send),
        "hl deposit": tg.normalise_hl_ledger_entry(deposit),
    }
    gas = tg.gas_funding_edges(A, {B: A})
    if gas:
        edges["gas"] = gas[0]
    corr = tg.correlation_edges(A, {B: {"deposit_amount_usd": 100.0,
                                        "deposit_ts": 1_790_000_000}})
    if corr:
        edges["correlation"] = corr[0]
    return edges


def test_every_edge_constructor_stays_at_or_under_ten_keys():
    built = _constructed_edges()
    assert {"substrate", "l1", "hl send", "hl deposit"} <= set(built)
    for name, edge in built.items():
        assert edge is not None, name
        assert len(edge) <= tg.MAX_EDGE_KEYS, (name, sorted(edge))
        assert "timestamp" not in edge, name


def test_the_bound_is_the_cliff_it_claims_to_be():
    ten = {f"k{i}": i for i in range(10)}
    eleven = {f"k{i}": i for i in range(11)}
    assert sys.getsizeof(eleven) > sys.getsizeof(ten) * 1.5


def test_edge_time_is_derived_from_ts_and_respects_a_stored_one():
    assert tg.edge_time({"ts": 1_790_000_000}) == "2026-09-21T14:13:20+00:00"
    assert tg.edge_time({"ts": 0}) is None and tg.edge_time({}) is None
    assert tg.edge_time({"ts": 1, "timestamp": "given"}) == "given"


def test_stored_edges_still_carry_their_text_time():
    edge = tg.normalise_transfer_record({"src": A, "dst": B, "chain": "ethereum", "asset": "USDC",
                                         "amount_usd": 1.0, "tx_hash": "0x9", "ts": 1_790_000_000})
    g = {"edge_count": 1, "nodes": [{"wallet": B, "edge_ids": [edge["id"]], "totals": {"edge_count": 1}}],
         "edges": [edge]}
    stored = tg.trim_edges_for_storage(g)["edges"][0]
    assert stored["timestamp"] == "2026-09-21T14:13:20+00:00"
    assert "timestamp" not in edge                    # the in-memory edge is untouched


def test_dedupe_keeps_first_seen_edges_by_reference_and_never_mutates_input():
    one = tg.normalise_transfer_record({"src": A, "dst": B, "chain": "ethereum", "asset": "USDC",
                                        "amount_usd": 1.0, "tx_hash": "0xa", "ts": 200})
    two = tg.normalise_transfer_record({"src": B, "dst": A, "chain": "ethereum", "asset": "USDC",
                                        "amount_usd": 1.0, "tx_hash": "0xb", "ts": 100})
    out = tg.dedupe_edges([one, two])
    assert out[0] is two and out[1] is one            # no copy of an edge nothing changed


def test_dedupe_copies_only_what_a_duplicate_changes():
    first = {"id": "x", "ts": 200, "src": A, "dst": B}
    earlier = {"id": "x", "ts": 150, "src": A, "dst": B}
    earliest = {"id": "x", "ts": 120, "src": A, "dst": B}
    out = tg.dedupe_edges([first, earlier, earliest])
    assert len(out) == 1 and out[0]["ts"] == 120
    assert out[0] is not first
    assert first["ts"] == 200 and earlier["ts"] == 150          # inputs untouched


def test_a_later_duplicate_changes_nothing_and_copies_nothing():
    first = {"id": "x", "ts": 100}
    out = tg.dedupe_edges([first, {"id": "x", "ts": 300}])
    assert out[0] is first and first["ts"] == 100
