"""When the node budget binds, WHICH wallets enter the graph must mean something.

`build_graph` walks breadth-first and stops at `max_nodes`. Inside a level it
admitted neighbours in adjacency order — the order edges were read off disk —
so once the cap bit, file order decided who got in. Measured on the live
substrate 2026-09-16: every depth-1 counterparty fits, but at depth 2 only 41
of 266 non-service wallets were admitted, and of those carrying at least $1M
of flow **3 got in and 89 did not** — while $0 airdrop senders held places.

The `expanded_ledger` lesson, a fourth time: when a cap trims a collection, ask
what the sort order MEANS. For admission it should be how much value moved
between a wallet and the level that reached it; an edge we could not value
ranks after one we could, and the address is only a tiebreaker.
"""

from src.transfer_graph import build_graph
from tests.test_transfer_graph import BASE_TS, T, edges_from, l1, node_for

HUB = "0x1111111111111111111111111111111111111111"
BIG = "0xffffffffffffffffffffffffffffffffffffff01"


def _small(i):
    return f"0x{i + 0x100:040x}"


def test_a_large_second_hop_beats_earlier_small_ones_for_the_last_places():
    rows = [l1(T, HUB, 5_000_000, BASE_TS)]
    rows += [l1(HUB, _small(i), 10, BASE_TS + 10 + i) for i in range(20)]
    rows.append(l1(HUB, BIG, 4_000_000, BASE_TS + 100))
    graph = build_graph(edges_from(*rows), T, max_nodes=6)
    assert node_for(graph, BIG) is not None


def test_an_unvalued_edge_ranks_after_a_valued_one():
    rows = [l1(T, HUB, 5_000_000, BASE_TS), l1(HUB, BIG, 50, BASE_TS + 100)]
    edges = edges_from(*rows)
    # Earlier in time, so storage order would admit it first.
    unvalued = dict(edges[1], id="u", dst=_small(1), amount_usd=None, ts=BASE_TS + 1)
    graph = build_graph([edges[0], unvalued, edges[1]], T, max_nodes=3)
    assert node_for(graph, BIG) is not None
    assert node_for(graph, _small(1)) is None


def test_without_a_binding_cap_every_neighbour_is_still_admitted():
    rows = [l1(T, HUB, 5_000_000, BASE_TS)]
    rows += [l1(HUB, _small(i), 10 * (i + 1), BASE_TS + 10 + i) for i in range(8)]
    graph = build_graph(edges_from(*rows), T, max_nodes=500)
    assert all(node_for(graph, _small(i)) is not None for i in range(8))
