"""Historical graph completion is separate from a recipient's next observation."""

from src import transfer_graph as graph

TARGET = "0x" + "1" * 40
RELAY = "0x" + "2" * 40
NEXT = "0x" + "3" * 40


def test_due_recipient_is_reswept_and_later_forwarding_is_discovered(monkeypatch):
    from src.chain import collect
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test")
    monkeypatch.setattr(graph, "load_config", lambda: {"target_wallet": TARGET, "chains": []})
    monkeypatch.setattr(graph, "_swept_for_graph", set)
    calls = []
    monkeypatch.setattr(collect, "sweep_wallet", lambda wallet, *a, **k: calls.append(wallet) or {"status": "ok"})
    monkeypatch.setattr(collect, "records_for", lambda wallet: [{
        "id": "later", "chain": "arbitrum", "src": RELAY, "dst": NEXT, "ts": 99_000,
        "amount_usd": 900_000, "amount": 900_000, "asset": "USDC", "kind": "erc20", "tx_hash": "later"}])
    edge = graph.normalise_l1_transfer({"from": TARGET, "to": RELAY, "value": "1000000000000",
                                      "hash": "funding", "timeStamp": "1000", "tokenSymbol": "USDC"})
    rows, diagnostics = graph.expand_frontier([edge], TARGET,
        {**graph.DEFAULTS, "max_expansions": 1}, now_ts=100_000, already_expanded=[RELAY],
        refresh_state={RELAY: {"last_successful_read": 2000, "next_check": 3000, "depth": 1}})
    assert calls == [RELAY]
    assert any(row["dst"] == NEXT for row in rows)
    assert diagnostics["refresh_state"][RELAY]["last_successful_read"] == 100_000
    assert diagnostics["refreshed_wallets"] == [RELAY]


def test_legacy_expanded_wallets_gain_staggered_schedules_without_mass_resweep():
    from src.surveillance import schedule_refreshes
    wallets = ["0x" + f"{i:040x}" for i in range(1, 101)]
    due, state = schedule_refreshes(wallets, [], TARGET, {}, 100_000, 5)
    assert due == []
    assert len(state) == 100
    assert len({row["next_check"] for row in state.values()}) > 50


def test_refresh_budget_is_fair_to_the_oldest_due_wallets():
    from src.surveillance import schedule_refreshes
    previous = {RELAY: {"next_check": 20, "depth": 1}, NEXT: {"next_check": 10, "depth": 2}}
    due, _state = schedule_refreshes([RELAY, NEXT], [], TARGET, previous, 100, 1)
    assert [row["wallet"] for row in due] == [NEXT]
