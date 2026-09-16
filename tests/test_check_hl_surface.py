"""The HL-surface script: strict reads, honest failures, bounded time, transitions.

Network-free: every read goes through an injected `post`, and nothing here
touches real `data/`.
"""

from datetime import UTC, datetime

from scripts import check_hl_surface as chk
from src import alerts, hl_surface

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
WATCHED = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
CAND = "0x7fdafde5cfb5465924316eced2d3715494c517d1"
SUB = "0x8bea775107aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
REFERRER = "0x4c78a97cef589b01bb91dbf893fffa14243d2444"
NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)

CONFIG = {"target_wallet": TARGET, "known_self_wallets": [SELF],
          "watch_wallets": [{"address": WATCHED}],
          "hl_shared_destinations": ["0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"]}
ROSTER = {"wallets": [
    {"wallet": CAND, "tier": "POSSIBLE"},
    {"wallet": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "tier": "INFRASTRUCTURE",
     "is_service": True},
]}

EMPTY_REFERRAL = {"referredBy": None, "cumVlm": "0.0",
                  "referrerState": {"stage": "needToCreateCode"}}


def _fake(answers: dict, fail: set = frozenset()):
    """answers[(type, user)] -> payload; (type, user) in `fail` raises."""
    calls = []

    def post(body):
        key = (body["type"], body["user"])
        calls.append(key)
        if key in fail:
            raise RuntimeError("info endpoint unavailable: timeout")
        default = {"subAccounts": None, "userVaultEquities": [],
                   "referral": EMPTY_REFERRAL}[body["type"]]
        return answers.get(key, default)
    post.calls = calls
    return post


class _Clock:
    def __init__(self, step=0.0):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _run(post, previous=None, budget=100.0, clock=None):
    return chk.run(CONFIG, ROSTER, previous, post, now=NOW, budget_seconds=budget,
                   clock=clock or _Clock(), sleep=lambda _s: None)


def test_the_cluster_and_pinned_wallets_come_first_and_services_are_skipped():
    wallets = chk.wallets_to_check(CONFIG, ROSTER)
    assert wallets[:3] == [TARGET, SELF, WATCHED]
    assert CAND in wallets
    assert "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48" not in wallets


def test_a_failed_read_is_null_and_counted_and_does_not_erase_the_others():
    post = _fake({("subAccounts", CAND): [{"subAccountUser": SUB, "master": CAND,
                                           "name": "funding-test",
                                           "clearinghouseState": {"marginSummary": {
                                               "accountValue": "1263075.0"}}}]},
                 fail={("userVaultEquities", CAND)})
    report = _run(post)
    reading = report["readings"][CAND]
    assert reading["vaults"] is None
    assert reading["subaccounts"][0]["address"] == SUB
    assert report["unreadable"] == 1
    assert report["subaccounts"][SUB]["account_value"] == 1263075.0


def test_a_measured_absence_is_empty_not_null():
    report = _run(_fake({}))
    assert report["readings"][TARGET] == {
        "subaccounts": [], "vaults": [],
        "referral": {"referred_by": None, "code": None, "referred": []}}
    assert report["unreadable"] == 0


def test_the_time_budget_leaves_the_rest_listed_as_not_reached():
    report = _run(_fake({}), budget=2.5, clock=_Clock(step=1.0))
    assert report["not_reached"]
    for wallet in report["not_reached"]:
        assert wallet not in report["readings"]
    assert report["wallets_checked"] == len(report["readings"])


def test_referrer_sizes_are_measured_and_then_cached():
    post = _fake({("referral", CAND): {**EMPTY_REFERRAL,
                                       "referredBy": {"referrer": REFERRER, "code": "XYZSET9"}},
                  ("referral", REFERRER): {"referredBy": None, "referrerState": {
                      "stage": "ready", "data": {"code": "XYZSET9", "referralStates": [
                          {"user": CAND}, {"user": SUB}]}}}})
    report = _run(post)
    assert report["referrer_sizes"][REFERRER]["accounts"] == 2
    assert report["referrals"][CAND] == {"referrer": REFERRER, "referrer_accounts": 2}

    before = len(post.calls)
    cached = _run(post, previous=report)
    assert ("referral", REFERRER) not in post.calls[before:]
    assert cached["referrals"][CAND]["referrer_accounts"] == 2


def test_a_failed_referrer_measurement_stays_unmeasured():
    post = _fake({("referral", CAND): {**EMPTY_REFERRAL,
                                       "referredBy": {"referrer": REFERRER, "code": "X"}}},
                 fail={("referral", REFERRER)})
    report = _run(post)
    assert REFERRER not in report["referrer_sizes"]
    assert report["referrals"][CAND]["referrer_accounts"] is None


def test_a_new_cluster_subaccount_alerts_once(monkeypatch):
    fired = []
    monkeypatch.setattr(chk, "alert_cluster_subaccount",
                        lambda link, detail: fired.append(("sub", link["address"])) or True)
    monkeypatch.setattr(chk, "alert_cluster_referral",
                        lambda link: fired.append(("ref", link["address"])) or True)
    post = _fake({("subAccounts", SELF): [{"subAccountUser": SUB, "master": SELF,
                                           "name": "new",
                                           "clearinghouseState": {"marginSummary": {
                                               "accountValue": "5.0"}}}]})
    report = _run(post)
    chk.fire_alerts(None, report)
    assert fired == [("sub", SUB)]
    fired.clear()
    chk.fire_alerts(report, report)
    assert fired == []


def test_a_candidate_only_pair_raises_no_alert(monkeypatch):
    fired = []
    monkeypatch.setattr(chk, "alert_cluster_subaccount", lambda *a: fired.append(a))
    monkeypatch.setattr(chk, "alert_cluster_referral", lambda *a: fired.append(a))
    report = {"links": [{"kind": "referral_pair", "address": CAND, "linked_to": SUB}],
              "subaccounts": {}}
    chk.fire_alerts(None, report)
    assert fired == []


def test_the_alerts_use_routable_severities(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append(subject) or True)
    alerts.alert_cluster_subaccount(
        {"kind": "subaccount", "address": SUB, "linked_to": SELF, "why": "x"},
        {"master": SELF, "name": "new", "account_value": 5.0})
    alerts.alert_cluster_referral(
        {"kind": "referral", "address": CAND, "linked_to": TARGET, "why": "x",
         "code_accounts": 1})
    assert sent[0].startswith("[EZEKIEL] CRITICAL:")
    assert sent[1].startswith("[EZEKIEL] HIGH:")


def test_main_writes_only_where_it_is_told(monkeypatch, tmp_path):
    monkeypatch.setattr(hl_surface, "HL_SURFACE_DIR", tmp_path / "hl_surface")
    monkeypatch.setattr(chk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(chk, "load_config", lambda: CONFIG)
    monkeypatch.setattr(chk, "strict_read", _fake({}))
    monkeypatch.setattr(chk, "fire_alerts", lambda previous, report: [])
    monkeypatch.setattr(chk.time, "sleep", lambda _s: None)
    assert chk.main() == 0
    assert (tmp_path / "hl_surface" / "latest.json").exists()


def test_referrer_measurement_stops_on_the_same_deadline():
    """The referrer pass is bounded too: a stalled referrer read must not carry
    the step past its timeout. Left unmeasured, it simply casts no vote."""
    post = _fake({("referral", CAND): {**EMPTY_REFERRAL,
                                       "referredBy": {"referrer": REFERRER, "code": "X"}}})
    # Enough budget to read every wallet, none left for referrers.
    wallets = len(chk.wallets_to_check(CONFIG, ROSTER))
    clock = _Clock(step=1.0)
    report = _run(post, budget=wallets + 0.5, clock=clock)
    assert report["not_reached"] == []
    assert ("referral", REFERRER) not in post.calls
    assert report["referrals"][CAND]["referrer_accounts"] is None


def test_a_link_whose_alert_failed_is_retried_next_run(monkeypatch):
    """A failed send consumes no cooldown and stays queued. Diffing against the
    previous report alone would lose it: the link is already on file by then."""
    outcome = [False]
    fired = []
    monkeypatch.setattr(chk, "alert_cluster_subaccount",
                        lambda link, detail: fired.append(link["address"]) or outcome[0])
    link = {"kind": "subaccount", "address": SUB, "linked_to": SELF, "why": "x"}
    report = {"links": [link], "subaccounts": {SUB: {"master": SELF}}}
    assert chk.fire_alerts(None, report) == [link]
    report["undelivered_alerts"] = [link]
    outcome[0] = True
    assert chk.fire_alerts(report, {"links": [link], "subaccounts": {}}) == []
    assert fired == [SUB, SUB]
