# tests/test_check_hyperevm.py
"""The HyperEVM tripwire fires once, on the right transition, and never on a
failed read.

Getting the trigger condition wrong has two distinct failure modes and both are
bad: alerting on `nonce > 0` fires forever once a wallet is active, and alerting
when the previous nonce is unknown fires on the first run for a wallet that has
been busy for a year. Only 0 -> non-zero is the event.
"""

import json

import scripts.check_hyperevm as check

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
OTHER = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"


def _setup(monkeypatch, tmp_path, activities, previous=None):
    monkeypatch.setattr(check, "HYPEREVM_DIR", tmp_path / "hyperevm")
    monkeypatch.setattr(check, "load_config", lambda: {
        "target_wallet": TARGET, "known_self_wallets": []})
    monkeypatch.setattr(check, "account_activity",
                        lambda addr, **kw: dict(activities[addr.lower()]))
    fired = []
    monkeypatch.setattr(check, "alert_hyperevm_activation",
                        lambda w, label, n, p: fired.append((w, n, p)) or True)
    saved = {}
    monkeypatch.setattr(check, "save_latest",
                        lambda d, payload: saved.update(payload))
    if previous is not None:
        monkeypatch.setattr(check, "_previous", lambda: previous)
    return fired, saved


def _act(nonce, errors=()):
    return {"address": TARGET, "nonce": nonce, "native_wei": 0,
            "has_code": False, "errors": list(errors)}


def test_no_alert_while_the_wallet_stays_idle(monkeypatch, tmp_path):
    fired, saved = _setup(monkeypatch, tmp_path, {TARGET: _act(0)},
                          previous={TARGET: {"nonce": 0}})
    assert check.main() == 0
    assert fired == []
    assert saved["activations_alerted"] == 0


def test_alerts_on_the_transition_from_never_acted_to_acted(monkeypatch, tmp_path):
    fired, saved = _setup(monkeypatch, tmp_path, {TARGET: _act(3)},
                          previous={TARGET: {"nonce": 0}})
    assert check.main() == 0
    assert fired == [(TARGET, 3, 0)]
    assert saved["activations_alerted"] == 1


def test_does_not_alert_on_the_first_ever_run(monkeypatch, tmp_path):
    """With no prior record a busy wallet would otherwise alert as though it had
    just activated. The baseline run records state; it does not accuse."""
    fired, _ = _setup(monkeypatch, tmp_path, {TARGET: _act(168)}, previous={})
    assert check.main() == 0
    assert fired == []


def test_does_not_re_alert_once_already_active(monkeypatch, tmp_path):
    fired, _ = _setup(monkeypatch, tmp_path, {TARGET: _act(200)},
                      previous={TARGET: {"nonce": 168}})
    assert check.main() == 0
    assert fired == []


def test_an_unreadable_nonce_alerts_nothing_and_is_counted(monkeypatch, tmp_path):
    """The load-bearing case. A rate-limited run must neither raise an alarm nor
    report the wallet as clear."""
    fired, saved = _setup(monkeypatch, tmp_path,
                          {TARGET: _act(None, ["eth_getTransactionCount: rate limited"])},
                          previous={TARGET: {"nonce": 0}})
    assert check.main() == 0
    assert fired == []
    assert saved["unreadable"] == 1
    assert saved["activations_alerted"] == 0


def test_state_is_persisted_for_the_next_run(monkeypatch, tmp_path):
    _, saved = _setup(monkeypatch, tmp_path, {TARGET: _act(0)}, previous={})
    assert check.main() == 0
    assert saved["wallets"][0]["nonce"] == 0
    assert saved["watched"] == 1
    assert "1000" in saved["rpc_limits"]


def test_previous_reads_back_what_was_saved(tmp_path, monkeypatch):
    """The two halves must agree on the file's shape, or every run looks like a
    first run and the tripwire never fires."""
    d = tmp_path / "hyperevm"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps(
        {"wallets": [{"address": TARGET, "nonce": 7}]}))
    monkeypatch.setattr(check, "HYPEREVM_DIR", d)
    assert check._previous()[TARGET]["nonce"] == 7


def test_previous_survives_a_corrupt_file(tmp_path, monkeypatch):
    d = tmp_path / "hyperevm"
    d.mkdir(parents=True)
    (d / "latest.json").write_text("{not json")
    monkeypatch.setattr(check, "HYPEREVM_DIR", d)
    assert check._previous() == {}


def test_watched_wallets_includes_known_self_wallets_without_duplicates():
    got = check.watched_wallets({"target_wallet": TARGET.upper(),
                                 "known_self_wallets": [OTHER, TARGET, ""]})
    assert [a for a, _ in got] == [TARGET, OTHER]
