import json

from src.discovery_store import DiscoveryStore
from src.market_discovery import collect_once, collect_stream, select_markets
from tests.test_discovery_store import A, B, trade


def test_poll_is_snapshot_coverage_and_failure_does_not_erase(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        result = collect_once({"target_wallet": A, "discovery": {"markets": ["BTC"], "max_markets": 1}},
                              store, fetch=lambda body: [trade()], now_ms=2000)
        assert result["inserted"] == 1
        assert result["coverage"]["continuous"] is False
        assert [r["wallet"] for r in store.candidates(now_ms=2000)] == [B]
        failed = collect_once({"discovery": {"markets": ["BTC"], "max_markets": 1}}, store,
                              fetch=lambda body: {"ok": False, "error": "down"}, now_ms=3000)
        assert failed["errors"]
        assert store.export_summary()["events_retained"] == 1


def test_market_rotation_reserves_exploration_and_includes_hip3():
    config = {"discovery": {"markets": ["BTC", "xyz:NVDA"], "max_markets": 3}}
    universe = ["BTC", "xyz:NVDA", "ETH", "SOL", "HYPE", "xyz:GOLD"]
    first = select_markets(config, universe, rotation=0)
    second = select_markets(config, universe, rotation=1)
    assert "xyz:NVDA" in first
    assert len(first) <= 3
    assert first != second


def test_stream_reconnect_preserves_events_marks_gap_and_is_bounded(tmp_path):
    connections = []
    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def send(self, body):
            assert json.loads(body)["method"] == "subscribe"
        def recv(self, timeout=None):
            if len(connections) == 1:
                raise OSError("disconnect")
            return json.dumps({"channel": "trades", "data": [trade()]})
    def connect(*a, **k):
        connections.append(True)
        return Socket()
    with DiscoveryStore(tmp_path / "store.db") as store:
        result = collect_stream({"discovery": {"markets": ["BTC"], "max_markets": 1}}, store,
                                connect=connect, fetch=lambda body: [trade()],
                                seconds=10, max_messages=1, max_reconnects=2,
                                sleep=lambda _: None, clock=lambda: 2)
        assert len(connections) == 2
        assert result["messages"] == 1
        assert store.export_summary()["events_retained"] == 1
        assert any(o.get("reason") == "disconnect" for o in store.coverage()["observations"])


def test_scanner_reserves_exploration_without_balance_floor(tmp_path, monkeypatch):
    from src import scanner
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    config = {"target_wallet": A, "known_self_wallets": [],
              "discovery": {"scan_budget": 1}, "scanner": {}}
    with DiscoveryStore(tmp_path / ".local" / "discovery.sqlite3") as store:
        store.ingest_trades([trade(timestamp=2_000_000)], 2_000_000)
    monkeypatch.setattr(scanner.time, "time", lambda: 2000)
    targets = scanner.discovery_targets(config)
    assert list(targets) == [B]
    assert targets[B]["source"] == "public_trades"
