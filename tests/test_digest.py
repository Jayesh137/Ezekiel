"""The daily digest: one quiet message a day so silence has a meaning
(src/digest.py, scripts/send_digest.py, alerts.send_notice)."""

from datetime import UTC, datetime

from src import digest

NOW = datetime(2026, 10, 13, 7, 5, tzinfo=UTC)
TARGET = "0x" + "a" * 40
KNOWN = "0x" + "b" * 40
CONFIG = {"target_wallet": TARGET, "known_self_wallets": [KNOWN]}


def _readings(**over):
    base = {
        "dormancy": {"dormancy": {"silent_days": 1, "unusual": False, "unprecedented": False,
                                  "stats": {"p90": 7, "max": 21}},
                     "handoffs": {}},
        "account_value": {"total": 57_090_961.98},
        "fills_newest_ms": int(NOW.timestamp() * 1000) - 7 * 3600 * 1000,
        "roster": {"wallets": [
            {"wallet": KNOWN, "tier": "CONFIRMED"},
            {"wallet": "0x" + "1" * 40, "tier": "PROBABLE"},
            {"wallet": "0x" + "2" * 40, "tier": "POSSIBLE"},
            {"wallet": "0x" + "3" * 40, "tier": "WATCH"},
        ]},
        "casebook": {"cases": [
            {"address": KNOWN, "known": True, "p_now": 0.5},
            {"address": "0x" + "4" * 40, "excluded": True, "p_now": 0.4},
            {"address": "0x" + "5" * 40, "p_now": 0.0021, "hl": {"on_hl": True, "value": 5754.8},
             "headline": "One operator with 0xb2f7374b..."},
            {"address": "0x" + "6" * 40, "p_now": 0.0019, "hl": {"on_hl": False, "value": 0.0}},
        ]},
        "watchlist": {"wallets": [
            {"address": "0x" + "7" * 40, "source": "config", "why": "a question under observation",
             "account_value": 57_000_000, "last_fill_ms": int(NOW.timestamp() * 1000) - 3600 * 1000},
            {"address": KNOWN, "source": "roster", "why": "roster CONFIRMED (0.84): ...",
             "account_value": 444.38, "last_fill_ms": int(NOW.timestamp() * 1000) - 40 * 86400 * 1000},
            {"address": "0x" + "8" * 40, "source": "roster", "why": "roster CONFIRMED (0.7): ...",
             "account_value": 0.01, "last_fill_ms": None},
        ]},
        "census": {"population": 2, "ratio_p99": 0.0, "min_clips": 3, "rho_population": 0,
                   "attempted": 252, "comparable_upper": 0.0285},
        "tape_census": {"sketch_wallets": 2900, "attempted": 300, "comparable_upper": 0.041,
                        "hits": [{"wallet": "0x" + "9" * 40}]},
        "tape": {"newcomer_handoffs": []},
        "execution": {"wallets_checked": 29, "candidates": 60,
                      "matches": [{"wallet": "0x" + "c" * 40, "discriminating": True},
                                  {"wallet": "0x" + "d" * 40, "discriminating": False}]},
        "alerts": {"healthy": True, "undelivered": 0, "recent": [
            {"at": "2026-10-12T09:07:17+00:00", "subject": "[EZEKIEL] HIGH: Wallet Discovery Stalled (85.7h)"},
            {"at": "2026-10-10T09:07:17+00:00", "subject": "[EZEKIEL] CRITICAL: old"},
            {"at": "2026-10-13T01:00:00+00:00", "subject": "[EZEKIEL] INFO: quiet"},
        ]},
        "feed_other": {"blind_since": {}},
        "feed_watch": {"blind_since": {"circle_flows": "2026-10-12T20:00:00+00:00"}},
        "index": {"last_updated": "2026-10-13T06:51:00+00:00"},
        "risk": {"level": "LOW", "score": 14.2},
        "scans": {"policy": "OBSERVING"},
    }
    base.update(over)
    return base


def test_due_once_a_day_from_the_digest_hour():
    assert digest.due(None, NOW) is True
    assert digest.due("2026-10-12", NOW) is True
    assert digest.due("2026-10-13", NOW) is False
    early = datetime(2026, 10, 13, 6, 59, tzinfo=UTC)
    assert digest.due("2026-10-12", early) is False


def test_build_answers_the_three_morning_questions():
    built = digest.build(_readings(), CONFIG, NOW)
    assert built["target"]["last_fill"] == "7h ago"
    assert built["target"]["unusual"] is False and built["target"]["value"] > 5e7
    # Tiers count beyond his known wallets; the casebook top skips known/excluded.
    assert built["hunt"]["tiers"] == {"CONFIRMED": 0, "PROBABLE": 1, "POSSIBLE": 1}
    assert [s["address"][:4] for s in built["hunt"]["suspects"]] == ["0x55", "0x66"]
    assert built["hunt"]["watched"] == 3
    # The set the owner should follow: his known wallets and every CONFIRMED one,
    # never a wallet watched as a question.
    assert [w["address"][:4] for w in built["hunt"]["his_wallets"]] == ["0xbb", "0x88"]
    # The execution gate reads the merged census: the tape's larger ceiling wins.
    assert built["execution"]["gate_open"] is True
    assert built["execution"]["ceiling"] == 0.041
    assert built["execution"]["discriminating"] == 1
    # Health: only CRITICAL/HIGH in the last 24 h count as pages; blind feeds are named.
    assert built["health"]["paged_24h"] == ["HIGH: Wallet Discovery Stalled (85.7h)"]
    assert built["health"]["blind_feeds"] == ["circle_flows"]
    assert built["health"]["data_age"] == "14m ago"


def test_render_fits_a_phone_and_leads_with_the_target():
    title, body = digest.render(digest.build(_readings(), CONFIG, NOW))
    assert title == "Ezekiel daily 2026-10-13"
    lines = body.splitlines()
    assert lines[0].startswith("Target: last fill 7h ago; silence 1d, normal (p90 7d); value $57.1M")
    assert "His other wallets: 0xbbbbbbbb… traded 40.0d ago, $444; 0x88888888… no fill seen, $0." in body
    assert "Hunt: 0 confirmed / 1 probable / 1 possible" in body
    assert "Top suspects: 0x55555555…" in body and "(HL $6K)" in body and "not on HL" in body
    assert "Execution vote gate OPEN: false-match ceiling 4.1%" in body
    assert "BLIND: circle_flows" in body
    assert "Paged last 24h: HIGH: Wallet Discovery Stalled" in body
    assert len(lines) <= 9


def test_an_unusual_silence_is_said_loudly_and_a_closed_gate_plainly():
    readings = _readings(
        dormancy={"dormancy": {"silent_days": 9, "unusual": True, "unprecedented": False,
                               "stats": {"p90": 7, "max": 21}}, "handoffs": {"0x1": {}}},
        census={"population": 2, "attempted": 50, "comparable_upper": None},
        tape_census=None, alerts=None, index=None, execution=None, tape=None,
    )
    _, body = digest.render(digest.build(readings, CONFIG, NOW))
    assert "UNUSUAL silence 9d (his p90 7d)" in body
    assert "1 dormancy handoff(s)" in body
    assert "Execution vote gate closed: false-match ceiling unmeasured" in body
    assert "alerts FAILING" in body            # an unreadable alert record is never "healthy"
    assert "data unknown" in body
    assert "Paged last 24h: nothing." in body


def test_missing_files_never_break_the_digest():
    empty = dict.fromkeys(_readings().keys())
    title, body = digest.render(digest.build(empty, None, NOW))
    assert title and "Target: last fill unknown" in body


def test_send_notice_bypasses_the_severity_gate_at_low_priority_and_records_no_health(monkeypatch, tmp_path):
    import src.alerts as alerts_mod
    posted = []

    class R:
        status_code = 200

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("NTFY_TOPIC", "ezekiel-test")
    monkeypatch.delenv("NTFY_INCLUDE_INFO", raising=False)
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: (posted.append((url, kw)), R())[1])
    recorded = []
    monkeypatch.setattr(alerts_mod, "_record_delivery", lambda *a, **k: recorded.append(a))

    via = alerts_mod.send_notice("Ezekiel daily 2026-10-13", "body")
    assert sorted(via) == ["ntfy", "telegram"]
    ntfy_kw = next(kw for u, kw in posted if u.endswith("/ezekiel-test"))
    assert ntfy_kw["headers"]["Priority"] == "low"
    telegram_kw = next(kw for u, kw in posted if "telegram" in u)
    assert telegram_kw["json"]["disable_notification"] is True
    assert recorded == []


def test_send_notice_reports_a_rejected_channel_without_raising(monkeypatch):
    import src.alerts as alerts_mod

    class R:
        status_code = 500

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "ezekiel-test")
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **kw: R())
    assert alerts_mod.send_notice("t", "b") == []


def test_the_script_sends_once_and_remembers_the_day(monkeypatch, tmp_path):
    from scripts import send_digest as sd
    monkeypatch.setattr(sd, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sd, "OUT_DIR", tmp_path / "digest")
    monkeypatch.setattr(sd, "load_readings", lambda: _readings())
    monkeypatch.setattr(sd, "load_config", lambda: CONFIG)
    state = tmp_path / "state"
    monkeypatch.setattr(sd, "read_cursor_text", lambda name: (state / f"{name}.txt").read_text()
                        if (state / f"{name}.txt").exists() else None)
    monkeypatch.setattr(sd, "write_cursor_text",
                        lambda name, value: (state.mkdir(exist_ok=True), (state / f"{name}.txt").write_text(value)))
    sent = []
    import src.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "send_notice", lambda t, b: (sent.append(t), ["ntfy"])[1])

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(sd, "datetime", _Now)
    assert sd.main([]) == 0 and len(sent) == 1
    assert (state / "digest_sent_day.txt").read_text() == "2026-10-13"
    assert (tmp_path / "digest" / "latest.json").exists()
    assert sd.main([]) == 0 and len(sent) == 1                  # same day: not again
    assert sd.main(["--force"]) == 0 and len(sent) == 2


def test_an_undelivered_digest_is_retried_next_run(monkeypatch, tmp_path):
    from scripts import send_digest as sd
    monkeypatch.setattr(sd, "OUT_DIR", tmp_path / "digest")
    monkeypatch.setattr(sd, "load_readings", lambda: _readings())
    monkeypatch.setattr(sd, "load_config", lambda: CONFIG)
    monkeypatch.setattr(sd, "read_cursor_text", lambda name: None)
    written = []
    monkeypatch.setattr(sd, "write_cursor_text", lambda name, value: written.append(value))
    import src.alerts as alerts_mod
    monkeypatch.setattr(alerts_mod, "send_notice", lambda t, b: [])
    assert sd.main([]) == 0
    assert written == []


def test_discovery_and_the_continuous_tape_are_in_the_digest():
    graph = {"health": {"expansion": {"status": "budget_exhausted", "lookups": 5, "new_edges": 4295,
                                      "frontier_eligible": 71015,
                                      "wallets_expanded": ["0x" + "1" * 40, "0x" + "2" * 40],
                                      "refreshed_wallets": ["0x" + "1" * 40]}}}
    tape = {"source": "sqd", "window_seconds": 1208.5, "newcomer_handoffs": [],
            "cofire": {"hits": 2, "his_windows": 7}}
    d = digest.build(_readings(graph=graph, tape=tape), CONFIG, now=NOW)
    assert d["discovery"] == {"status": "budget_exhausted", "lookups": 5, "new_wallets": 1,
                              "refreshed": 1, "new_edges": 4295, "pending": 71015,
                              "last_expansion_at": None}
    assert d["execution"]["cofire_hits"] == 2 and d["execution"]["tape_source"] == "sqd"
    _, body = digest.render(d)
    assert "Discovery: 5 lookup(s) last run, 1 new wallet(s) walked, 1 re-read" in body
    assert "Tape (20 min continuous)" in body and "2 co-firing hit(s)" in body
    d2 = digest.build(_readings(), CONFIG, now=NOW)
    assert "Discovery: no graph reading." in digest.render(d2)[1]


def test_replica_and_treasury_lines_appear_only_with_a_reading():
    from src import digest
    readings = _readings()
    assert digest.build(readings, {}, NOW)["replica"] is None
    assert digest.build(readings, {}, NOW)["treasury"] is None
    readings = _readings(
        replica={"dataset": {"behind_blocks": 100_800, "backfill_complete": False,
                             "backfill_segments_done": 3}, "users": ["0x" + "1" * 40],
                 "agents_total": 4, "outside_total": 2, "agent_reused": [{"agent": "0x" + "a" * 40}]},
        treasury={"summary": {"wallets_seen": 900}, "sessions_read": 10, "sessions_total": 63,
                  "hits": [], "known_wallets": {"0x" + "2" * 40: {"sessions_with": 2, "hit": False},
                                                "0x" + "3" * 40: {"sessions_with": 0}}})
    built = digest.build(readings, {}, NOW)
    assert built["replica"]["reused"] == 1 and built["treasury"]["controls_present"] == 1
    _, body = digest.render(built)
    assert "Action history: 1 wallets, 4 agents ever approved, 2 outside destinations, 1 agent(s) reused" in body
    assert "feed 2h behind, history 3 segments in" in body
    assert "Treasury sessions: 10/63 read, 900 accounts seen, 0 hit(s); known wallets present in 1/2, 0 hit." in body
