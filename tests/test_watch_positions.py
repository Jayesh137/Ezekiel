"""The watch's position notices: baseline first, one notice per run, a stored book."""

import json

from scripts import check_watchlist as check
from src import utils

T = "0x" + "e" * 40
K = "0x" + "f" * 40


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    (tmp_path / "watchlist").mkdir()


def test_first_reading_is_a_baseline_and_the_next_run_sends_one_notice(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    sent = []
    import src.alerts as alerts
    monkeypatch.setattr(alerts, "send_notice", lambda t, b, priority="low": sent.append((t, b, priority)) or ["ntfy"])
    config = {"target_wallet": T, "known_self_wallets": [K]}
    book_t = {"ZEC": {"size": -500.0, "side": "short", "value": 20_000.0}}
    book_k = {"BTC": {"size": 1.0, "side": "long", "value": 70_000.0}}

    doc = check.notify_positions({T: book_t, K: book_k}, config)
    assert sent == [], "a first reading is a baseline"
    assert doc["books"] == {T: book_t, K: book_k}
    stored = json.loads((tmp_path / "watchlist" / "positions.json").read_text())
    assert stored["books"][T] == book_t and stored["aggregate"]["ZEC"]["side"] == "short"

    # Next run: he closes ZEC on the target; the known wallet is unreadable (absent).
    doc = check.notify_positions({T: {}}, config)
    assert len(sent) == 1
    title, body, priority = sent[0]
    assert title == "He moved: 1 position change(s)" and priority == "default"
    assert body == "target:\n  CLOSED short ZEC 500 ($20K)"
    assert doc["books"][K] == book_k, "an unread wallet keeps its stored book"
    assert doc["changes"] == {T: [{"kind": "closed", "coin": "ZEC",
                                   "before": {"size": -500.0, "side": "short", "value": 20_000.0},
                                   "after": None}]}


def test_no_change_sends_nothing_and_the_switch_withholds(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    sent = []
    import src.alerts as alerts
    monkeypatch.setattr(alerts, "send_notice", lambda *a, **k: sent.append(a) or ["ntfy"])
    config = {"target_wallet": T}
    book = {"ZEC": {"size": -500.0, "side": "short", "value": 20_000.0}}
    check.notify_positions({T: book}, config)
    check.notify_positions({T: book}, config)
    assert sent == []
    monkeypatch.setenv("POSITION_NOTICES", "0")
    check.notify_positions({T: {}}, config)
    assert sent == []
    assert check.notify_positions({}, config) is None


def test_target_account_value_keeps_his_book_for_the_notice(monkeypatch):
    state = {"marginSummary": {}, "assetPositions": [
        {"position": {"coin": "ZEC", "szi": "-500", "positionValue": "20000", "entryPx": "40"}}]}
    monkeypatch.setattr(check, "account_value", lambda addr, dexes: (12.0, state))
    monkeypatch.setattr(check, "live_hip3_dexes", lambda: [])
    check.TARGET_BOOK.clear()
    assert check.target_account_value({"target_wallet": T.upper()}) == 12.0
    assert check.TARGET_BOOK[T]["ZEC"]["side"] == "short"
    check.TARGET_BOOK.clear()
