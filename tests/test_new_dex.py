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
