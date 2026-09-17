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
