# tests/test_frontier_rebuild.py
"""The frontier is a function of the edges and the ledger, not a fragile queue.

Two defects found together on 2026-10-13, both of which left discovery dead while
every run reported `ok` with 0 lookups:

1. A wallet at depth 2 or deeper could enter the walk only through the persisted
   `frontier_queue`, written by the run that expanded its parent. Once the queue
   reached zero (truncation, a lost file, a migration) nothing ever re-queued the
   children of finished work — the parent was in the ledger for ever. Production
   read `queue 0` from at least 2026-10-07 while the stored edges named 182
   unexplored recipients of explored wallets.
2. The refresh schedule's ten most overdue entries were all services. The walk
   suppressed each without recording the check, so the same ten came back on
   every run and no refresh ever happened.
"""

import time

import pytest

from src import transfer_graph as tg
from src.transfer_graph import normalise_l1_transfer

T = "0x45d26f28196d226497130c4bac709d808fed4029"
NOW = time.time()


def addr(seed: str) -> str:
    return "0x" + (seed * 40)[:40]


def l1(src, dst, usd, hours_ago, ref):
    return normalise_l1_transfer({
        "from": src, "to": dst, "value": str(int(usd * 1e6)),
        "timeStamp": str(int(NOW - hours_ago * 3600)),
        "hash": ref, "tokenSymbol": "USDC"})


def queued(diag):
    return [q["wallet"] for q in diag["frontier_queue"]]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(tg, "_swept_for_graph", set)


def test_children_of_explored_wallets_are_requeued_without_a_persisted_queue():
    """The exact production shape: parent expanded long ago, queue lost, child
    never looked at. It must be pending again, at its real depth."""
    a, b = addr("a"), addr("b")
    edges = [l1(T, a, 1_000_000, 50, "0xta"), l1(a, b, 950_000, 40, "0xab")]
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 0}, now_ts=NOW,
        resume=[], already_expanded=[a])
    assert queued(diag) == [b]
    assert diag["frontier_queue"][0]["depth"] == 2
    assert diag["frontier_reseeded"] == 1
    assert diag["frontier_eligible"] == 1


def test_a_rebuilt_child_is_expanded_not_only_queued(monkeypatch):
    a, b, c = addr("a"), addr("b"), addr("c")
    swept = []
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *args, **kw: swept.append(wallet) or {"status": "ok"})
    monkeypatch.setattr("src.chain.collect.records_for", lambda w, **kw: [{
        "id": "bc", "chain": "arbitrum", "src": b, "dst": c, "ts": int(NOW - 3600),
        "amount_usd": 900_000, "amount": 900_000, "asset": "USDC", "kind": "erc20",
        "tx_hash": "0xbc"}])
    edges = [l1(T, a, 1_000_000, 50, "0xta"), l1(a, b, 950_000, 40, "0xab")]
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 1}, now_ts=NOW,
        resume=[], already_expanded=[a])
    assert swept == [b], "the lost child must be walked, not just listed"
    assert diag["wallets_expanded"] == [b]
    assert c in queued(diag), "and its own recipients join the frontier"


def test_rebuilding_never_requeues_finished_or_service_wallets():
    a, b, svc = addr("a"), addr("b"), addr("5")
    edges = [l1(T, a, 1_000_000, 50, "0xta"), l1(a, b, 950_000, 40, "0xab"),
             l1(a, svc, 10_000, 30, "0xas")]
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 0}, now_ts=NOW,
        resume=[], already_expanded=[a, b], known_services={svc})
    assert queued(diag) == []
    assert diag["frontier_reseeded"] == 0


def test_rebuilding_respects_the_depth_ceiling():
    a, b, c = addr("a"), addr("b"), addr("c")
    edges = [l1(T, a, 1_000_000, 50, "0xta"), l1(a, b, 950_000, 40, "0xab"),
             l1(b, c, 900_000, 30, "0xbc")]
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 0, "max_depth": 2}, now_ts=NOW,
        resume=[], already_expanded=[a, b])
    assert queued(diag) == [], "c sits at depth 3, beyond a ceiling of 2"


def test_a_child_already_in_the_persisted_queue_is_counted_once():
    a, b = addr("a"), addr("b")
    edges = [l1(T, a, 1_000_000, 50, "0xta"), l1(a, b, 950_000, 40, "0xab")]
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 0}, now_ts=NOW,
        resume=[{"wallet": b, "depth": 2}], already_expanded=[a])
    assert queued(diag) == [b]
    assert diag["frontier_reseeded"] == 0, "it was never lost"


def test_a_parent_reached_outside_the_walk_uses_its_recorded_depth():
    """A ledger wallet with no path over walkable edges (gas funding, HL ledger)
    still has children; its refresh row remembers the depth."""
    a, b = addr("a"), addr("b")
    edges = [l1(a, b, 950_000, 40, "0xab")]  # no edge from the target to `a`
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 0}, now_ts=NOW,
        resume=[], already_expanded=[a],
        refresh_state={a: {"depth": 2, "next_check": NOW + 99_999}})
    assert queued(diag) == [b]
    assert diag["frontier_queue"][0]["depth"] == 3


# --- the refresh livelock ----------------------------------------------------

def test_services_due_for_refresh_do_not_hold_the_refresh_slice(monkeypatch):
    """Ten overdue services and one overdue wallet, one slot: the wallet gets it."""
    swept = []
    monkeypatch.setattr("src.chain.collect.sweep_wallet",
                        lambda wallet, *args, **kw: swept.append(wallet) or {"status": "ok"})
    monkeypatch.setattr("src.chain.collect.records_for", lambda w, **kw: [])
    real = addr("e")
    services = ["0x" + f"{i:040x}" for i in range(1, 11)]   # sort before `real`
    edges = [l1(T, real, 1_000_000, 50, "0xtr")]
    edges += [l1(T, s, 1_000, 60 + i, f"0xs{i}") for i, s in enumerate(services)]
    state = {s: {"next_check": 10, "depth": 1} for s in services}
    state[real] = {"next_check": 20, "depth": 1}
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 4}, now_ts=NOW,   # slice of 1
        resume=[], already_expanded=services + [real], known_services=set(services),
        refresh_state=state)
    assert swept == [real]
    assert diag["refreshed_wallets"] == [real]
    assert diag["refresh_state"][real]["last_successful_read"] == NOW


def test_schedule_refreshes_skips_named_addresses():
    from src.surveillance import schedule_refreshes
    svc, real = addr("1"), addr("2")
    previous = {svc: {"next_check": 10, "depth": 1}, real: {"next_check": 20, "depth": 1}}
    due, _ = schedule_refreshes([svc, real], [], T, previous, 100, 1, skip={svc})
    assert [row["wallet"] for row in due] == [real]


# --- refreshes may not starve the frontier ------------------------------------

def test_refreshes_stop_at_their_time_share_so_new_wallets_are_walked(monkeypatch):
    """Ten due refreshes, each slow; the frontier's new wallet must still get a lookup.

    Live, 2026-10-13 01:29 UTC: the first run after the rebuild made 4 lookups,
    all refreshes, and 70,235 pending wallets waited another run.
    """
    clock = {"now": 1000.0}
    monkeypatch.setattr(tg.time, "monotonic", lambda: clock["now"])
    swept = []

    def slow_sweep(wallet, *args, **kw):
        swept.append(wallet)
        clock["now"] += 30.0          # each lookup costs 30 "seconds"
        return {"status": "ok"}
    monkeypatch.setattr("src.chain.collect.sweep_wallet", slow_sweep)
    monkeypatch.setattr("src.chain.collect.records_for", lambda w, **kw: [])
    olds = ["0x" + f"{i:040x}" for i in range(1, 11)]
    new = addr("e")
    edges = [l1(T, o, 10_000, 100 + i, f"0xo{i}") for i, o in enumerate(olds)]
    edges += [l1(T, olds[0], 1_000_000, 50, "0xbig"), l1(olds[0], new, 950_000, 40, "0xnew")]
    state = {o: {"next_check": 10, "depth": 1} for o in olds}
    _, diag = tg.expand_frontier(
        edges, T, {**tg.DEFAULTS, "max_expansions": 40, "time_budget_seconds": 120},
        now_ts=NOW, resume=[], already_expanded=olds, refresh_state=state)
    assert new in swept, f"the new wallet never got a lookup: {swept}"
    # A refresh may START while under the half-way mark, so three 30 s refreshes
    # fit a 120 s walk (0, 30, 60 s); the fourth, at 90 s, is deferred.
    assert len([w for w in swept if w in olds]) == 3, swept
    assert swept[-1] == new, "the new wallet is walked after the refreshes stop"
    assert any(d["action"] == "deferred" and "refresh share" in d["reason"]
               for d in diag["decisions"])
