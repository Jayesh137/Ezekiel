# tests/test_watchlist.py
"""Close watch on a wallet that is probably his and is not confirmed.

A watched wallet is not a `known_self_wallet`: that list is operator ground
truth, this one is a question under observation. The distinction is what stops
a lead promoting itself into the cluster it is being compared against.
"""

from src import watchlist as wl

W = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
DAY = 86_400_000


def test_watched_accepts_addresses_or_objects_and_lowercases():
    got = wl.watched({"watch_wallets": [
        "0xABC", {"address": "0xDEF", "why": "born in his silence"}, {"nope": 1}, 7]})
    assert [w["address"] for w in got] == ["0xabc", "0xdef"]
    assert got[1]["why"] == "born in his silence"
    assert wl.watched({}) == []


def test_the_first_reading_is_a_baseline_and_alerts_nothing():
    current = wl.snapshot(W, account_value=51_310_835.0, hyperevm_nonce=0)
    assert wl.changes(None, current) == []


def test_a_big_move_in_either_direction_is_reported():
    before = wl.snapshot(W, account_value=51_000_000.0)
    grew = wl.changes(before, wl.snapshot(W, account_value=80_000_000.0))
    fell = wl.changes(before, wl.snapshot(W, account_value=20_000_000.0))
    steady = wl.changes(before, wl.snapshot(W, account_value=49_000_000.0))
    assert [c["kind"] for c in grew] == ["value_move"] and "grew" in grew[0]["detail"]
    assert [c["kind"] for c in fell] == ["value_move"] and "fell" in fell[0]["detail"]
    assert steady == []


def test_emptying_is_its_own_finding():
    got = wl.changes(wl.snapshot(W, account_value=51_000_000.0),
                     wl.snapshot(W, account_value=12.0))
    assert [c["kind"] for c in got] == ["emptied"]


def test_new_agents_subaccounts_and_withdrawal_destinations_are_each_reported():
    before = wl.snapshot(W, agents=["0xa1"], subaccounts=[], withdrawal_destinations=[])
    after = wl.snapshot(W, agents=["0xa1", "0xA2"], subaccounts=["0xs1"],
                        withdrawal_destinations=["0xd1"])
    kinds = {(c["kind"], c.get("address")) for c in wl.changes(before, after)}
    assert kinds == {("new_agent", "0xa2"), ("new_subaccount", "0xs1"),
                     ("new_withdrawal_destination", "0xd1")}


def test_hyperevm_activation_is_only_the_confirmed_transition():
    zero = wl.snapshot(W, hyperevm_nonce=0)
    assert [c["kind"] for c in wl.changes(zero, wl.snapshot(W, hyperevm_nonce=3))] \
        == ["hyperevm_activated"]
    # Unknown before, or unreadable now, is not a transition.
    assert wl.changes(wl.snapshot(W, hyperevm_nonce=None),
                      wl.snapshot(W, hyperevm_nonce=3)) == []
    assert wl.changes(zero, wl.snapshot(W, hyperevm_nonce=None)) == []


def test_going_quiet_is_reported_once_not_every_half_hour():
    now = 100 * DAY
    trading = wl.snapshot(W, last_fill_ms=now - DAY // 2)
    quiet = wl.snapshot(W, last_fill_ms=now - 4 * DAY)
    first = wl.changes(trading, quiet, now_ms=now)
    assert [c["kind"] for c in first] == ["went_quiet"]
    # Still quiet on the next run: already reported, so nothing new.
    assert wl.changes(quiet, quiet, now_ms=now) == []


def test_an_unreadable_run_never_manufactures_a_change():
    before = wl.snapshot(W, account_value=51_000_000.0, hyperevm_nonce=0)
    broken = wl.snapshot(W, read_ok=False, errors=["timeout"])
    assert wl.changes(before, broken) == []


def test_contacts_name_what_the_counterparty_is():
    world = {T: "the target",
             "0xdeposit": "a private deposit address of his",
             "0xprob": "roster: PROBABLE"}
    got = wl.contacts(["0xstranger", T.upper(), "0xdeposit", None, ""], world)
    # Sorted by address, so the target's 0x45… precedes the fixture's 0xd….
    assert got == [{"address": T, "is": "the target"},
                   {"address": "0xdeposit", "is": "a private deposit address of his"}]
    assert wl.contacts([], world) == []


def test_the_report_names_what_could_not_be_read():
    snaps = [wl.snapshot(W, account_value=1.0),
             wl.snapshot("0xbad", read_ok=False, errors=["boom"])]
    report = wl.build_report(snaps, {"changes": {}, "contacts": {}})
    assert report["watched"] == 2 and report["unreadable"] == ["0xbad"]


def test_a_busy_contact_is_shared_infrastructure_not_a_connection():
    hits = [{"address": "0xbusy", "is": "roster: POSSIBLE"},
            {"address": "0xquiet", "is": "a private deposit address of his"},
            {"address": "0xunknown", "is": "roster: POSSIBLE"}]
    people, infra = wl.shared_infrastructure(
        hits, {"0xbusy": True, "0xquiet": False, "0xunknown": None})
    # Unmeasured still alerts: "we could not tell" is not "it is an exchange".
    assert [h["address"] for h in people] == ["0xquiet", "0xunknown"]
    assert [h["address"] for h in infra] == ["0xbusy"]
    assert wl.shared_infrastructure([], {}) == ([], [])


def test_only_his_own_addresses_are_critical():
    assert wl.contact_severity("the target") == "CRITICAL"
    assert wl.contact_severity("a known wallet of his") == "CRITICAL"
    assert wl.contact_severity("a private deposit address of his") == "CRITICAL"
    assert wl.contact_severity("roster: POSSIBLE") == "HIGH"
    assert wl.contact_severity(None) == "CRITICAL"


def test_hyperliquid_naming_an_owner_is_reported_and_confirms_alone():
    before = wl.snapshot(W, role="user")
    after = wl.snapshot(W, role="agent", owner="0xOWNER")
    got = wl.changes(before, after)
    kinds = {c["kind"] for c in got}
    assert kinds == {"explicit_link_owner", "role_change"}
    link = wl.explicit_links(got)
    assert [c["address"] for c in link] == ["0xowner"]
    # A staking link and a master are the same class of evidence.
    assert wl.explicit_links(wl.changes(
        wl.snapshot(W), wl.snapshot(W, staking_link="0xSTAKE")))[0]["address"] == "0xstake"
    assert wl.explicit_links(wl.changes(
        wl.snapshot(W), wl.snapshot(W, master="0xMASTER")))[0]["kind"] == \
        "explicit_link_master"
    assert wl.explicit_links([]) == []


def test_an_unchanged_link_is_not_re_reported():
    steady = wl.snapshot(W, role="agent", owner="0xowner")
    assert wl.changes(steady, steady) == []


def test_a_book_on_a_new_dex_is_a_migration_inside_hyperliquid():
    before = wl.snapshot(W, dexes=["perp", "xyz"])
    after = wl.snapshot(W, dexes=["perp", "xyz", "FLX"])
    assert [(c["kind"], c["address"]) for c in wl.changes(before, after)] == \
        [("new_dex", "flx")]
    # The first time the field is read at all is a baseline, not an event.
    assert wl.changes(wl.snapshot(W, account_value=1.0), after) == []
    assert wl.snapshot(W)["dexes"] is None and wl.snapshot(W)["vaults_led"] is None


def test_leading_a_vault_is_reported():
    got = wl.changes(wl.snapshot(W, vaults_led=[]), wl.snapshot(W, vaults_led=["0xV1"]))
    assert [(c["kind"], c["address"]) for c in got] == [("new_vault_led", "0xv1")]
