"""The feature path evaluated offline must be the one production can observe."""

import gzip
import json
from datetime import UTC, datetime

from src import backtest, calibration, fingerprint, scanner
from src import thresholds as th

DAY = 86_400_000


def order(oid, status, timestamp, *, wallet="a", **fields):
    return {"wallet": wallet, "status": status, "statusTimestamp": timestamp,
            "order": {"oid": oid, "timestamp": timestamp, "coin": "BTC", "tif": "Ioc",
                      "orderType": "Limit", **fields}}


def fills(days=24):
    return [{"time": (day + 1) * DAY + i * 1000, "tid": day * 100 + i,
             "oid": day * 100 + i, "coin": "BTC", "sz": "1", "px": "100",
             "side": "B", "dir": "Open Long", "startPosition": "0", "closedPnl": "0"}
            for day in range(days) for i in range(8)]


def test_recent_fingerprint_uses_orders_in_its_observed_window(monkeypatch):
    now = 25 * DAY
    monkeypatch.setattr(fingerprint._time, "time", lambda: now / 1000)
    monkeypatch.setattr(fingerprint, "load_positions_latest", dict)
    monkeypatch.setattr(fingerprint, "load_orders", lambda: [
        order(1, "filled", 24 * DAY), order(2, "canceled", DAY), order(3, "canceled", 26 * DAY)])
    result = fingerprint.build_fingerprint_recent(fills(), lookback_days=21)
    assert result["order_profile"]["orders"] == 1
    assert result["order_profile"]["cancel_rate"] == 0
    assert result["scoring_schema"] == th.SCORING_SCHEMA
    assert scanner.compare_order_profile(result, result) == 1


def test_lifecycle_events_count_one_order_and_open_is_not_cancellation():
    profile = fingerprint.compute_order_profile([order(1, "open", 1), order(1, "filled", 2),
                                                 order(2, "open", 3), order(3, "rejected", 4)])
    assert profile["orders"] == 3
    assert profile["cancel_rate"] == 0
    assert profile["terminal_orders"] == 2


def test_order_identity_includes_account_and_handles_reordered_observations():
    orders = [order(1, "filled", 3), order(1, "open", 1), order(1, "canceled", 2, wallet="b")]
    profile = fingerprint.compute_order_profile(orders)
    assert profile["orders"] == 2
    assert profile["cancel_rate"] == .5


def test_unknown_and_open_only_statuses_have_no_cancellation_measurement():
    profile = fingerprint.compute_order_profile([order(1, "open", 1), order(2, "unknown", 2)])
    assert profile["cancel_rate"] is None
    assert scanner.compare_order_profile({"order_profile": profile}, {"order_profile": profile}) == 1
    assert scanner.compare_order_profile({"order_profile": {"orders": 5}}, {"order_profile": {"orders": 5}}) is None


def test_unavailable_asof_dimensions_are_excluded_from_similarity():
    fp = scanner.build_candidate_fingerprint(fills(), {})
    fp["excluded_dimensions"] = ["account_size", "leverage_profile"]
    _, dimensions, _ = scanner.compute_similarity(fp, fp, {"high": .9, "medium": .8, "low": .65})
    assert dimensions["account_size"] is None
    assert dimensions["leverage_profile"] is None


def test_backtest_cannot_pass_with_no_strangers_or_borrow_latest_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backtest, "REPORT_PATH", tmp_path / "backtest.json")
    monkeypatch.setattr(fingerprint, "load_fills", fills)
    monkeypatch.setattr(fingerprint, "load_orders", list)
    monkeypatch.setattr(fingerprint, "load_positions_latest", lambda: (_ for _ in ()).throw(
        AssertionError("current account state must never enter historical validation")))
    monkeypatch.setattr(scanner, "compute_similarity", lambda *args: (.8, {"activity": .8}, {"vetoes": []}))
    import src.alerts as alerts
    monkeypatch.setattr(alerts, "alert_scorer_unreliable", lambda *args: None)
    report = backtest.run_backtest()
    assert report["passed"] is not True
    assert report["strangers_scored"] == 0
    assert report["validation_coverage"]["minimum_strangers"] == backtest.MIN_STRANGERS
    assert report["validation_coverage"]["historical_positions_available"] is False


def test_calibration_deduplicates_wallets_and_preserves_schema_and_feature_mask(tmp_path, monkeypatch):
    path = tmp_path / "population.json"
    monkeypatch.setattr(calibration, "POPULATION_PATH", path)
    sample = {"wallet": "0x" + "1" * 40, "score": .7, "feature_mask": ["activity"]}
    calibration.record_population_scores([sample])
    calibration.record_population_scores([{**sample, "score": .8}])
    assert calibration.load_population() == [.8]
    saved = json.loads(path.read_text())["samples"][-1]
    assert saved["wallet"] == sample["wallet"]
    assert saved["scoring_schema"] == th.SCORING_SCHEMA
    assert saved["feature_mask"] == ["activity"]


def test_anonymous_legacy_scores_cannot_activate_independent_wallet_gate(tmp_path, monkeypatch):
    path = tmp_path / "population.json"
    path.write_text(json.dumps({"samples": [{"score": .8}] * 1000}))
    monkeypatch.setattr(calibration, "POPULATION_PATH", path)
    assert calibration.load_population() == []
    assert not calibration.gate_active()


def test_historical_state_uses_prior_archived_snapshot_and_never_future_state(tmp_path):
    from src.backtest import load_state_asof
    archive = tmp_path / "archive"
    archive.mkdir()
    state = {"marginSummary": {"accountValue": "5"}, "assetPositions": []}
    with gzip.open(archive / "2026-09-20.jsonl.gz", "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"t": "12-00", "d": {"perp": state, "hip3": {}}}) + "\n")
        stream.write(json.dumps({"t": "14-00", "d": {"perp": {**state, "marginSummary": {"accountValue": "999"}}}}) + "\n")
    cutoff = int(datetime(2026, 9, 20, 13, tzinfo=UTC).timestamp() * 1000)
    result, coverage = load_state_asof(cutoff, tmp_path)
    assert result["marginSummary"]["accountValue"] == "5"
    assert coverage["observed_at_ms"] <= cutoff
    assert coverage["age_ms"] == 3_600_000
    assert load_state_asof(cutoff - 86_400_000, tmp_path)[0] == {}


def test_roster_rejects_validation_from_retired_schema(tmp_path, monkeypatch):
    from src import roster
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path / "data")
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "backtest.json").write_text(json.dumps({"passed": True, "self_score": .8,
                                                      "scoring_schema": "retired"}))
    assert roster.behavioural_is_trustworthy() is False


def test_roster_uses_same_adapted_behavioural_gate(tmp_path, monkeypatch):
    from src import roster
    data = tmp_path / "data"
    monkeypatch.setattr(roster, "DATA_DIR", data)
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "backtest.json").write_text(json.dumps({
        "passed": True, "self_score": .67, "scoring_schema": th.SCORING_SCHEMA}))
    (data / "candidates").mkdir(parents=True)
    (data / "candidates" / "latest.json").write_text(json.dumps({"candidates": [{
        "wallet": "0x" + "3" * 40, "latest_score": .61, "latest_scoring_schema": th.SCORING_SCHEMA,
        "latest_evidence": {"vetoes": []}}]}))
    result = roster.build_roster({"target_wallet": "0x" + "1" * 40})
    assert "behavioural" in result["wallets"][0]["vectors"]


def test_backtest_enriches_empty_order_profiles_and_deduplicates_negative_wallets(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backtest, "REPORT_PATH", tmp_path / "backtest.json")
    monkeypatch.setattr(fingerprint, "load_fills", fills)
    monkeypatch.setattr(fingerprint, "load_orders", list)
    monkeypatch.setattr(backtest, "load_state_asof", lambda cutoff: (
        {}, {"available": True, "observed_at_ms": cutoff}))
    seen = []
    monkeypatch.setattr(scanner, "get_candidate_orders", lambda wallet: [order(1, "filled", 20 * DAY)])

    def compare(target, candidate, *_args):
        if candidate.get("test_negative"):
            seen.append(candidate["order_profile"])
            return .1, {"activity": .8}, {"vetoes": []}
        return .8, {"activity": .8}, {"vetoes": []}

    monkeypatch.setattr(scanner, "compute_similarity", compare)
    (tmp_path / "scans").mkdir()
    row = {"wallet": "0x" + "2" * 40, "fingerprint": {"order_profile": {}, "test_negative": True}}
    (tmp_path / "scans" / "latest.json").write_text(json.dumps({"results": [row, row]}))
    result = backtest.run_backtest()
    assert seen and all(profile["orders"] == 1 for profile in seen)
    assert result["strangers_scored"] == 1
    assert result["passed"] is None
