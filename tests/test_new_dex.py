# tests/test_new_dex.py
"""A book opening on a HIP-3 dex he has never used.

That is what a migration INSIDE Hyperliquid looks like — no L1 trace, no
transfer, no new address, just the same account trading somewhere the owner
is not watching. The collector has been reading every live dex for the target
on every run since 2026-09-11 and storing the ones with a book in
`data/account/latest.json` under `hip3`; nothing compared one run's set with
the next until 2026-09-12. The close watch does exactly this for a watched
wallet (`dexes` in `watchlist.changes`); the target, the primary subject, had
no such alarm.

Live on 2026-09-12 he uses exactly one: `xyz` ($6.76M, four positions), out of
ten the venue lists."""

from src import alerts, collector

T = "0x45d26f28196d226497130c4bac709d808fed4029"


def _acct(dexes, value=1.0):
    return {"perp": {}, "spot": {},
            "hip3": {d: {"marginSummary": {"accountValue": str(value)}} for d in dexes}}


# --- the pure diff ----------------------------------------------------------

def test_a_dex_he_has_never_traded_is_new():
    assert collector.new_dexes(["xyz"], _acct(["xyz", "flx"])) == ["flx"]


def test_the_dex_he_always_uses_is_not_news():
    assert collector.new_dexes(["xyz"], _acct(["xyz"])) == []


def test_dropping_a_dex_is_not_a_new_dex():
    assert collector.new_dexes(["xyz", "flx"], _acct(["xyz"])) == []


def test_several_new_dexes_are_all_reported_in_order():
    assert collector.new_dexes([], _acct(["vntl", "abcd"])) == ["abcd", "vntl"]


def test_a_first_reading_is_a_baseline_not_news():
    """Rule: report the TRANSITION. With no stored set, every dex he has ever
    used would look new and the first run after deploy would cry wolf."""
    assert collector.new_dexes(None, _acct(["xyz"])) == []


def test_an_unreadable_account_is_not_an_empty_dex_set():
    """Rule 5: a failed read must never serialise as a clean result. An account
    file that cannot be parsed says nothing about which dexes he trades, and
    must not silently 'confirm' the stored set either."""
    for bad in (None, [], "nonsense", {"hip3": "nonsense"}):
        assert collector.new_dexes(["xyz"], bad) is None


def test_a_dex_carrying_no_book_is_not_being_used():
    """The collector only stores a dex with value or positions, but a stored
    entry that reads as empty is not a book — it is an artefact."""
    empty = {"hip3": {"xyz": {"marginSummary": {"accountValue": "1.0"}},
                      "flx": {"marginSummary": {"accountValue": "0.0"},
                              "assetPositions": []}}}
    assert collector.new_dexes(["xyz"], empty) == []


def test_a_dex_with_a_position_but_no_value_still_counts():
    held = {"hip3": {"flx": {"marginSummary": {"accountValue": "0.0"},
                             "assetPositions": [{"position": {"coin": "flx:AAPL"}}]}}}
    assert collector.new_dexes(["xyz"], held) == ["flx"]


# --- the alert --------------------------------------------------------------

def test_the_alert_is_critical_and_names_the_dex(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(
                            (key, hours, subject, body)) or True)
    assert alerts.alert_new_dex(T, ["flx"], ["xyz"])
    key, hours, subject, body = sent[0]
    assert alerts._severity_of(subject) == "CRITICAL"
    assert "flx" in subject and "flx" in body
    assert "xyz" in body                      # what he used before
    assert key == "new_dex_flx"


def test_each_new_dex_gets_its_own_cooldown_key(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(key) or True)
    alerts.alert_new_dex(T, ["flx", "vntl"], ["xyz"])
    assert sent == ["new_dex_flx_vntl"]


def test_no_new_dex_sends_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown", lambda *a: sent.append(a) or True)
    assert alerts.alert_new_dex(T, [], ["xyz"]) is False and sent == []


# --- the baseline the caller stores -----------------------------------------

def _wire_check(tmp_path, monkeypatch):
    """check_new_dex against a sandboxed data dir, collecting alerts."""
    import json

    from src import utils
    monkeypatch.setattr(collector, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collector, "load_config", lambda: {"target_wallet": T})
    (tmp_path / "account").mkdir(parents=True, exist_ok=True)
    fired = []
    monkeypatch.setattr(alerts, "alert_new_dex",
                        lambda w, fresh, known: fired.append((fresh, known)) or True)

    def write_account(dexes):
        (tmp_path / "account" / "latest.json").write_text(json.dumps(_acct(dexes)))

    return write_account, fired


def test_the_first_reading_stores_the_dexes_he_ALREADY_uses(tmp_path, monkeypatch):
    """The baseline must record the live set, not the empty diff.

    `new_dexes` correctly answers "no news" on a first reading, but it returns
    only the DIFF — so the caller, computing `known | fresh`, stored an empty
    set and threw the live reading away. Measured live 2026-09-12: the state
    file was written empty by the baseline run, the next run read it as "he
    trades no dexes", and `xyz` — the one dex he has used all along — fired
    `CRITICAL: Target Opened A Book On A New Dex (xyz)` at 07:28 UTC.

    A false CRITICAL is the expensive kind. It is how an operator learns to
    swipe the channel away, and this one landed on the alarm for a migration
    inside Hyperliquid.
    """
    from src.utils import read_cursor_text
    write_account, fired = _wire_check(tmp_path, monkeypatch)
    write_account(["xyz"])

    collector.check_new_dex()

    assert fired == []
    assert read_cursor_text("known_hip3_dexes") == "xyz"


def test_the_run_after_a_baseline_does_not_re_report_the_same_dex(tmp_path, monkeypatch):
    """The regression itself: two runs, same dex, no alert."""
    write_account, fired = _wire_check(tmp_path, monkeypatch)
    write_account(["xyz"])

    collector.check_new_dex()
    collector.check_new_dex()

    assert fired == []


def test_a_dex_opened_after_the_baseline_still_alerts(tmp_path, monkeypatch):
    """Seeding the baseline must not blunt the alarm it exists to arm."""
    from src.utils import read_cursor_text
    write_account, fired = _wire_check(tmp_path, monkeypatch)
    write_account(["xyz"])
    collector.check_new_dex()

    write_account(["xyz", "flx"])
    collector.check_new_dex()

    assert fired == [(["flx"], ["xyz"])]
    assert read_cursor_text("known_hip3_dexes") == "flx,xyz"


def test_an_unreadable_account_never_overwrites_the_stored_set(tmp_path, monkeypatch):
    """Rule 5: 'we could not tell' must not be stored as 'he trades nothing'."""
    import json

    from src.utils import read_cursor_text
    write_account, fired = _wire_check(tmp_path, monkeypatch)
    write_account(["xyz"])
    collector.check_new_dex()

    (tmp_path / "account" / "latest.json").write_text(json.dumps({"perp": {}}))
    collector.check_new_dex()

    assert read_cursor_text("known_hip3_dexes") == "xyz"
    assert fired == []
