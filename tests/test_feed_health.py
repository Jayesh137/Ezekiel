# tests/test_feed_health.py
"""A detector that stops writing must be reported, not mistaken for calm."""

import json
from datetime import UTC, datetime, timedelta

from src import feed_health as fh

NOW = datetime(2026, 9, 17, 3, 0, tzinfo=UTC)


def _write(root, rel, doc):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))


def test_a_fresh_feed_is_quiet_and_a_stale_one_is_reported(tmp_path):
    feeds = {"a": ("a/latest.json", "computed_at", 60), "b": ("b/latest.json", "computed_at", 60)}
    _write(tmp_path, "a/latest.json", {"computed_at": (NOW - timedelta(minutes=30)).isoformat()})
    _write(tmp_path, "b/latest.json", {"computed_at": (NOW - timedelta(minutes=90)).isoformat()})
    got = fh.assess(feeds, tmp_path, NOW)
    assert [p["feed"] for p in got] == ["b"] and got[0]["age_minutes"] == 90.0


def test_missing_unreadable_and_undated_are_all_problems(tmp_path):
    feeds = {"gone": ("gone/latest.json", "computed_at", 60),
             "broken": ("broken/latest.json", "computed_at", 60),
             "undated": ("undated/latest.json", "computed_at", 60)}
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "latest.json").write_text("{not json")
    _write(tmp_path, "undated/latest.json", {"other": 1})
    problems = {p["feed"]: p["problem"] for p in fh.assess(feeds, tmp_path, NOW)}
    assert problems["gone"] == "never written"
    assert problems["broken"].startswith("unreadable")
    assert "no readable computed_at" in problems["undated"]


def test_a_circle_cursor_far_behind_is_reported_even_when_the_run_is_fresh(tmp_path):
    _write(tmp_path, "circle_flows/latest.json", {"computed_at": NOW.isoformat(),
                                                   "lag_blocks": fh.CIRCLE_MAX_LAG_BLOCKS + 1})
    got = fh.assess({"Circle flows": fh.WATCH_FEEDS["Circle flows"]}, tmp_path, NOW)
    assert len(got) == 1 and "behind" in got[0]["problem"]


def test_every_live_feed_names_a_real_writer_and_a_limit_above_its_cadence():
    for name, (rel, key, limit) in {**fh.WATCH_FEEDS, **fh.OTHER_FEEDS}.items():
        assert rel.endswith("latest.json") and key and limit >= 360, name


def test_the_script_alerts_each_problem(tmp_path, monkeypatch):
    import scripts.check_feed_health as script
    import src.alerts as alerts
    from src import utils
    sent = []
    monkeypatch.setattr(alerts, "alert_feed_stale", lambda feed, problem, path: sent.append(feed) or True)
    assert script.main(["x", "watch"]) == 0
    assert set(sent) == set(fh.WATCH_FEEDS), "nothing was written in the sandbox, so every feed is missing"
    assert script.main(["x", "nonsense"]) == 2
    assert utils.DATA_DIR != tmp_path


def test_the_alert_routes_as_high(monkeypatch):
    import src.alerts as alerts
    subjects = []
    monkeypatch.setattr(alerts, "_send_with_cooldown", lambda k, h, s, b: subjects.append(s) or True)
    alerts.alert_feed_stale("roster", "last reading 13.0h ago (limit 12h)", "roster/latest.json")
    assert alerts._severity_of(subjects[0]) == "HIGH"


# --- fresh but blind ---------------------------------------------------------------
#
# A detector whose reads all fail, or whose parser stops understanding an
# endpoint, keeps writing `computed_at` on time. Staleness cannot see it.

def test_a_blind_reading_is_reported_only_after_it_has_lasted(tmp_path):
    feeds = {"shared agents": fh.OTHER_FEEDS["shared agents"]}
    _write(tmp_path, "agent_links/latest.json", {"computed_at": NOW.isoformat(),
                                                  "wallets_checked": 120, "agents_seen": 0,
                                                  "unreadable": 0})
    problems, since = fh.blind(feeds, tmp_path, {}, NOW)
    assert problems == [] and since == {"shared agents": NOW.isoformat()}, (
        "one blind run is a blip and must not wake anyone")

    later = NOW + timedelta(hours=fh.BLIND_HOURS)
    problems, since = fh.blind(feeds, tmp_path, since, later)
    assert [p["feed"] for p in problems] == ["shared agents"]
    assert "0 agents across 120 wallets" in problems[0]["problem"]
    assert since == {"shared agents": NOW.isoformat()}, "the clock must not restart while blind"


def test_a_feed_that_sees_again_restarts_the_clock(tmp_path):
    feeds = {"shared agents": fh.OTHER_FEEDS["shared agents"]}
    _write(tmp_path, "agent_links/latest.json", {"computed_at": NOW.isoformat(),
                                                  "wallets_checked": 120, "agents_seen": 214,
                                                  "unreadable": 0})
    problems, since = fh.blind(feeds, tmp_path, {"shared agents": "2026-01-01T00:00:00+00:00"}, NOW)
    assert problems == [] and since == {}


def test_each_blind_signal_names_what_it_saw():
    assert fh.BLIND_CHECKS["shared agents"]({"wallets_checked": 120, "unreadable": 60,
                                              "agents_seen": 5}) == "60 of 120 reads failed"
    assert fh.BLIND_CHECKS["HL account surface"]({"wallets_checked": 120, "unreadable": 0,
                                                   "subaccounts": {}}).startswith("0 sub-accounts")
    assert fh.BLIND_CHECKS["close watch"]({"watched": 3, "unreadable": ["a", "b", "c"]})
    assert fh.BLIND_CHECKS["close watch"]({"watched": 3, "unreadable": ["a"]}) is None
    assert "reads failing" in fh.BLIND_CHECKS["Circle flows"]({"error": "HTTPError: 403"})
    assert fh.BLIND_CHECKS["Circle flows"]({"blocks_read": 5_000, "deposits_read": 0,
                                             "withdrawals_read": 0}).startswith("0 Circle")
    # A short read with nothing in it is ordinary.
    assert fh.BLIND_CHECKS["Circle flows"]({"blocks_read": 600, "deposits_read": 0,
                                             "withdrawals_read": 0}) is None
    assert fh.BLIND_CHECKS["behavioural scan"]({"wallets_scanned": 0})
    assert fh.BLIND_CHECKS["amount correlation"]({"candidates_considered": 0})
    assert fh.BLIND_CHECKS["roster"]({"wallet_count": 0})
    assert fh.BLIND_CHECKS["dormancy handoff"]({"candidates_scored": 0})
    # Too few wallets for a zero to mean anything.
    assert fh.BLIND_CHECKS["shared agents"]({"wallets_checked": 3, "agents_seen": 0,
                                              "unreadable": 3}) is None


def test_every_blind_check_belongs_to_a_real_feed():
    assert set(fh.BLIND_CHECKS) <= set(fh.WATCH_FEEDS) | set(fh.OTHER_FEEDS)


def test_the_script_keeps_blind_state_per_group(tmp_path, monkeypatch):
    import scripts.check_feed_health as script
    import src.alerts as alerts
    from src import utils
    monkeypatch.setattr(alerts, "alert_feed_stale", lambda feed, problem, path: True)
    root = utils.DATA_DIR
    _write(root, "roster/latest.json", {"computed_at": datetime.now(UTC).isoformat(),
                                        "wallet_count": 0})
    assert script.main(["x", "other"]) == 0
    state = json.loads((root / "feed_health" / "other.json").read_text())
    assert list(state["blind_since"]) == ["roster"]
    assert not (root / "feed_health" / "watch.json").exists()
