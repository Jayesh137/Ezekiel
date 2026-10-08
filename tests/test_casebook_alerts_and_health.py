"""The wake alert routes by its severity; the casebook is a watched feed (spec §8, §12)."""

from src import alerts, feed_health

A = "0x" + "a" * 40
ROW = {"rank": 3, "p": 0.02, "p_now": 0.001, "p_ceiling": 0.3, "headline": "Paid his deposit address"}


def test_wake_alert_severity_cooldown_and_body(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, hours, subject, body)) or True)
    assert alerts.alert_casebook_wake(A, "hl_opened", {"total_value": 2e6, "week_volume": 5e5}, ROW, True)
    key, hours, subject, body = sent[0]
    assert key == f"casebook_hl_opened_{A}" and hours == 168
    assert "] CRITICAL:" in subject and "Paid his deposit address" in body
    assert "hypurrscan" in body.lower() and "$2,000,000" in body
    alerts.alert_casebook_wake(A, "hl_woke", {}, ROW, False)
    assert "] HIGH:" in sent[1][2] and sent[1][0] == f"casebook_hl_woke_{A}"


def test_the_casebook_is_a_watched_feed():
    assert feed_health.OTHER_FEEDS["casebook"] == ("casebook/latest.json", "computed_at", 720)


def test_blind_casebook_readings():
    check = feed_health.BLIND_CHECKS["casebook"]
    assert check({"counts": {"cases": 0}, "run": {"rows": 50}}) == "0 cases while the roster held 50 rows"
    assert "probes failed" in check({"counts": {"cases": 5}, "run": {"probes": {"attempted": 30, "failed": 20}}})
    assert check({"counts": {"cases": 5}, "run": {"probes": {"attempted": 30, "failed": 2}}}) is None
    assert check({"counts": {"cases": 5}, "run": {}}) is None


def test_the_casebook_is_never_compacted():
    from scripts.compact_data import IRREPLACEABLE
    assert "casebook" in IRREPLACEABLE


def test_the_test_guard_watches_the_casebook():
    from tests import conftest
    assert conftest._DIR_PROBES["the casebook"] == conftest.REAL_DATA_DIR / "casebook"
