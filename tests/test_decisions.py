"""The operator's decision queue: ranked, ruled-out, one command from settled."""

import json
from datetime import UTC, datetime

from scripts import casebook as cli
from src import decisions, digest

NOW = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)
T = "0x" + "a" * 40
K = "0x" + "b" * 40
S1, S2, S3, Q = ("0x" + c * 40 for c in "1234")


def _config(**over):
    base = {"target_wallet": T, "known_self_wallets": [K],
            "watch_wallets": [{"address": Q, "why": "a question", "added": "2026-09-01"}]}
    base.update(over)
    return base


def _casebook():
    return {"cases": [
        {"address": K, "known": "config:known_self", "p_now": 0.9},
        {"address": S1, "p_now": 0.005, "hl": {"on_hl": False, "value": 0.0}, "headline": "Money both ways"},
        {"address": S2, "p_now": 0.0054, "hl": {"on_hl": True, "value": 21e6}, "headline": "Handoff"},
        {"address": S3, "p_now": 0.0001, "hl": {"on_hl": True}, "headline": "too weak"},
        {"address": "0x" + "9" * 40, "excluded": "forgery", "p_now": 0.5},
    ]}


def test_pending_ranks_suspects_by_p_now_then_stale_questions_and_skips_ruled_and_known():
    queue = decisions.pending(_config(), _casebook(), NOW)
    assert [d["address"] for d in queue["pending"]] == [S2, S1, Q]
    assert queue["pending"][1]["ask"].startswith("Not a Hyperliquid account")
    assert queue["pending"][2]["kind"] == "watch_question" and queue["pending"][2]["age_days"] == 43
    assert queue["counts"] == {"suspects": 2, "watch_questions": 1} and queue["ruled"] == 0
    ruled = _config(casebook_rulings={S1.upper(): {"verdict": "not_him"}})
    queue = decisions.pending(ruled, _casebook(), NOW)
    assert [d["address"] for d in queue["pending"]] == [S2, Q] and queue["ruled"] == 1


def test_a_fresh_watch_question_is_not_yet_a_decision():
    config = _config(watch_wallets=[{"address": Q, "added": NOW.strftime("%Y-%m-%d")}])
    assert decisions.from_watch(config, NOW) == []
    assert decisions.from_watch(_config(watch_wallets=[Q]), NOW)[0]["age_days"] is None


def test_render_line_names_three_and_counts_the_rest():
    queue = decisions.pending(_config(), _casebook(), NOW, limit=5)
    line = decisions.render_line(queue, lambda a: a[:6])
    assert line.startswith("Needs your ruling: 0x2222 0.54% (HL); 0x1111 0.50% (not on HL); 0x4444 watched 43d")
    assert "casebook.py rule" in line
    assert decisions.render_line({"pending": []}, str) is None


def test_the_digest_carries_the_queue():
    readings = {"casebook": _casebook(), "dormancy": {}, "account_value": {}, "roster": {},
                "watchlist": {}, "alerts": {}, "index": {}}
    built = digest.build(readings, _config(), NOW)
    assert [d["address"] for d in built["decisions"]["pending"]] == [S2, S1, Q]
    _, body = digest.render(built)
    assert "Needs your ruling:" in body


def test_apply_ruling_writes_ground_truth_only_where_it_belongs():
    config = _config()
    cli.apply_ruling(config, S1.upper(), "not_him", "an exchange account", at="2026-10-14T00:00:00+00:00")
    assert config["casebook_rulings"][S1] == {"verdict": "not_him", "note": "an exchange account",
                                             "at": "2026-10-14T00:00:00+00:00"}
    assert config["known_self_wallets"] == [K]
    cli.apply_ruling(config, S2, "him", None, at="x")
    assert config["known_self_wallets"] == [K, S2]
    cli.apply_ruling(config, S2, "him", None, at="y")
    assert config["known_self_wallets"] == [K, S2], "idempotent"
    try:
        cli.apply_ruling(config, S3, "maybe", None, at="z")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown verdict must be refused")


def test_cmd_rule_rewrites_the_config_file(monkeypatch, tmp_path):
    from src import utils
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_config()))
    monkeypatch.setattr(utils, "CONFIG_PATH", path)
    assert cli.main(["rule", S1, "not_him", "--note", "exchange"]) == 0
    saved = json.loads(path.read_text())
    assert saved["casebook_rulings"][S1]["verdict"] == "not_him"
    assert cli.main(["rule", "nonsense", "him"]) == 2
