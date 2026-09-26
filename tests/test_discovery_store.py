import json

from src.discovery_store import DiscoveryStore

A = "0x" + "1" * 40
B = "0x" + "2" * 40


def trade(tid=1, coin="BTC", timestamp=1000, users=None):
    return {"coin": coin, "time": timestamp, "tid": tid, "px": "2", "sz": "3",
            "side": "B", "users": users or [A, B]}


def test_both_sides_composite_identity_and_self_trade(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        result = store.ingest_trades([trade(), trade(), trade(coin="ETH"), trade(2, users=[A, A])], 2000)
        assert result["inserted"] == 3
        assert result["duplicates"] == 1
        rows = {r["wallet"]: r for r in store.candidates(now_ms=2000)}
        assert rows[A]["trade_count"] == 3
        assert rows[B]["trade_count"] == 2
        assert rows[A]["notional_usd"] == 18
        assert rows[A]["first_seen_ms"] == 1000


def test_reopen_idempotent_and_invalid_rows_do_not_discard_valid(tmp_path):
    path = tmp_path / "store.db"
    with DiscoveryStore(path) as store:
        result = store.ingest_trades([trade(), {**trade(2), "px": "nan"}, trade(3, users=[A, "bad"])], 2000)
        assert result["rejected"] == 2
    with DiscoveryStore(path) as store:
        assert store.ingest_trades([trade()], 3000)["duplicates"] == 1
        assert store.candidates(now_ms=3000)[0]["trade_count"] == 1


def test_exclusion_blocks_enrichment_but_keeps_observations(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        store.set_exclusions([A])
        store.ingest_trades([trade()], 2000)
        assert [r["wallet"] for r in store.candidates(now_ms=2000)] == [B]
        assert store.export_summary()["wallets_observed"] == 2


def test_checked_wallet_yields_budget_to_another_small_account(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        store.ingest_trades([trade()], 2000)
        store.mark_checked(A, 2000, "ok")
        assert [r["wallet"] for r in store.candidates(limit=1, now_ms=2001)] == [B]


def test_compaction_preserves_aggregates_and_replay_is_not_double_counted(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        store.ingest_trades([trade(timestamp=1000), trade(2, timestamp=3000)], 4000)
        store.prune(before_ms=2000, max_events=100)
        result = store.ingest_trades([trade(timestamp=1000)], 5000)
        assert result["outside_retention"] == 1
        assert store.candidates(now_ms=5000)[0]["trade_count"] == 2
        assert store.export_summary()["events_retained"] == 1


def test_conflicting_event_identity_cannot_change_participants(tmp_path):
    with DiscoveryStore(tmp_path / "store.db") as store:
        store.ingest_trades([trade()], 2000)
        got = store.ingest_trades([trade(users=[A, "0x" + "3" * 40])], 3000)
        assert got["conflicts"] == 1
        assert len(store.candidates(now_ms=3000)) == 2


def test_import_jsonl_streaming_provenance_and_bad_lines(tmp_path):
    from src.market_discovery import import_jsonl
    path = tmp_path / "trades.jsonl"
    path.write_text(json.dumps(trade()) + "\nnot-json\n" + json.dumps({"channel": "trades", "data": [trade(2)]}))
    with DiscoveryStore(tmp_path / "store.db") as store:
        first = import_jsonl(path, store, now_ms=2000)
        second = import_jsonl(path, store, now_ms=3000)
        assert first["inserted"] == 2 and first["malformed_lines"] == 1
        assert second["duplicates"] == 2
        assert store.coverage()["observations"][-1]["source"] == "import:trades.jsonl"
