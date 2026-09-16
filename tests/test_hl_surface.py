"""What Hyperliquid itself declares about an account: sub-accounts, referral, vaults.

Until 2026-09-16 these three endpoints were asked about the TARGET alone, and
for him they are empty — so every overlap check built on them returned early
for every wallet. Measured over the 25 strongest roster wallets: 31 sub-account
addresses under 5 of them that no detector had ever seen (one holding $1.26M),
and a referral from one roster wallet to another.

The fixtures below are the real payload shapes read live that day.
"""

from datetime import UTC, datetime, timedelta

from src import hl_surface

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
MASTER = "0x7fdafde5cfb5465924316eced2d3715494c517d1"
SUB = "0x8bea775107aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HLP = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
CLUSTER = {TARGET, SELF}


def _sub(address, master, value="0.0", name="pm-test"):
    return {"name": name, "subAccountUser": address, "master": master,
            "clearinghouseState": {"marginSummary": {"accountValue": value}}}


def _referral(referrer=None, code=None, own_code=None, referred=()):
    doc = {"referredBy": ({"referrer": referrer, "code": code} if referrer else None),
           "cumVlm": "1.0", "referrerState": {"stage": "needToCreateCode"}}
    if own_code:
        doc["referrerState"] = {"stage": "ready", "data": {
            "code": own_code,
            "referralStates": [{"user": u, "cumVlm": "1.0"} for u in referred]}}
    return doc


# --- parsers: a failed read is None, a measured absence is [] -----------------

def test_null_subaccounts_is_the_api_saying_none():
    assert hl_surface.parse_subaccounts(None, MASTER) == []


def test_subaccount_rows_carry_master_name_and_value():
    rows = hl_surface.parse_subaccounts(
        [_sub(SUB.upper().replace("0X", "0x"), MASTER, "1263075.5", "funding-test")], MASTER)
    assert rows == [{"address": SUB, "master": MASTER, "name": "funding-test",
                     "account_value": 1263075.5}]


def test_an_unreadable_subaccount_payload_is_none_not_empty():
    assert hl_surface.parse_subaccounts({}, MASTER) is None
    assert hl_surface.parse_subaccounts("oops", MASTER) is None


def test_vault_addresses_are_parsed_and_a_failure_is_none():
    assert hl_surface.parse_vaults([{"vaultAddress": HLP.upper().replace("0X", "0x"),
                                     "equity": "1"}]) == [HLP]
    assert hl_surface.parse_vaults([]) == []
    assert hl_surface.parse_vaults({}) is None


def test_referral_reading_uses_the_keys_the_api_actually_returns():
    reading = hl_surface.referral_reading(
        _referral(referrer=MASTER, code="XYZSET9", own_code="MINE", referred=[SUB]))
    assert reading == {"referred_by": MASTER, "code": "MINE", "referred": [SUB]}


def test_an_empty_referral_payload_is_a_failed_read():
    """A successful read is always a populated document, so {} is hl_post's
    failure sentinel shape — the webData2 rule."""
    assert hl_surface.referral_reading({}) is None
    assert hl_surface.referral_reading(None) is None


# --- links -------------------------------------------------------------------

def _readings(**by_wallet):
    out = {}
    for wallet, parts in by_wallet.items():
        out[wallet] = {"subaccounts": parts.get("subaccounts", []),
                       "vaults": parts.get("vaults", []),
                       "referral": parts.get("referral",
                                             {"referred_by": None, "code": None,
                                              "referred": []})}
    return out


def _links(report, kind):
    return [(link["address"], link["linked_to"]) for link in report["links"]
            if link["kind"] == kind]


def test_a_subaccount_of_the_target_is_a_link_to_the_cluster():
    readings = {TARGET: {"subaccounts": [{"address": SUB, "master": TARGET, "name": "x",
                                          "account_value": 0.0}],
                         "vaults": [], "referral": None}}
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert _links(report, "subaccount") == [(SUB, TARGET)]
    assert report["subaccounts"][SUB]["master"] == TARGET


def test_a_cluster_wallet_that_is_a_candidates_subaccount_names_the_candidate():
    readings = {MASTER: {"subaccounts": [{"address": SELF, "master": MASTER, "name": "x",
                                          "account_value": 5.0}],
                         "vaults": [], "referral": None}}
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert _links(report, "subaccount") == [(SELF, MASTER)]


def test_a_candidates_own_subaccount_is_recorded_but_is_not_a_cluster_link():
    readings = {MASTER: {"subaccounts": [{"address": SUB, "master": MASTER, "name": "x",
                                          "account_value": 5.0}],
                         "vaults": [], "referral": None}}
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert report["subaccounts"][SUB]["master"] == MASTER
    assert _links(report, "subaccount") == []


def test_a_quiet_referral_from_the_cluster_is_a_link():
    readings = _readings(**{MASTER: {"referral": {"referred_by": TARGET, "code": None,
                                                  "referred": []}}})
    sizes = {TARGET: {"accounts": 2, "measured_at": "2026-09-16T00:00:00+00:00"}}
    report = hl_surface.build_report(readings, sizes, CLUSTER, set())
    assert _links(report, "referral") == [(MASTER, TARGET)]
    assert report["referrals"][MASTER] == {"referrer": TARGET, "referrer_accounts": 2}


def test_the_cluster_being_referred_by_a_candidate_is_a_link_too():
    readings = _readings(**{SELF: {"referral": {"referred_by": MASTER, "code": None,
                                                "referred": []}}})
    sizes = {MASTER: {"accounts": 1, "measured_at": "2026-09-16T00:00:00+00:00"}}
    report = hl_surface.build_report(readings, sizes, CLUSTER, set())
    assert _links(report, "referral") == [(SELF, MASTER)]


def test_an_account_on_a_cluster_wallets_own_quiet_code_is_a_link():
    """The account need not be a candidate: his code naming it is how we meet it.
    The code's size is read off his own reading, so no second call is needed."""
    readings = _readings(**{SELF: {"referral": {"referred_by": None, "code": "MINE",
                                                "referred": [SUB]}}})
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert _links(report, "referral") == [(SUB, SELF)]


def test_a_public_or_unmeasured_code_never_makes_a_referral_link():
    """Rule 9 for referrals: an influencer's code links everyone who used it."""
    readings = _readings(**{MASTER: {"referral": {"referred_by": TARGET, "code": None,
                                                  "referred": []}}})
    public = {TARGET: {"accounts": hl_surface.QUIET_REFERRALS + 1,
                       "measured_at": "2026-09-16T00:00:00+00:00"}}
    assert _links(hl_surface.build_report(readings, public, CLUSTER, set()),
                  "referral") == []
    unmeasured = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert _links(unmeasured, "referral") == []
    assert unmeasured["referrals"][MASTER]["referrer_accounts"] is None


def test_a_referral_between_two_strangers_is_only_a_pair():
    readings = _readings(**{MASTER: {"referral": {"referred_by": SUB, "code": None,
                                                  "referred": []}}})
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert _links(report, "referral") == []
    assert _links(report, "referral_pair") == [(MASTER, SUB)]


def test_shared_vaults_are_removed_from_deposits():
    other = "0x1111111111111111111111111111111111111111"
    readings = _readings(**{MASTER: {"vaults": [HLP, other]}})
    report = hl_surface.build_report(readings, {}, CLUSTER, {HLP})
    assert report["vault_deposits"] == {MASTER: [other]}


def test_an_unreadable_field_contributes_nothing_and_does_not_raise():
    readings = {MASTER: {"subaccounts": None, "vaults": None, "referral": None}}
    report = hl_surface.build_report(readings, {}, CLUSTER, set())
    assert report["links"] == [] and report["subaccounts"] == {}


# --- transitions -------------------------------------------------------------

def test_an_absent_previous_report_counts_as_empty_so_nothing_is_swallowed():
    link = {"kind": "subaccount", "address": SUB, "linked_to": TARGET, "why": "x"}
    assert hl_surface.new_links(None, {"links": [link]}) == [link]


def test_a_link_already_reported_is_not_new():
    link = {"kind": "subaccount", "address": SUB, "linked_to": TARGET, "why": "x"}
    assert hl_surface.new_links({"links": [dict(link)]}, {"links": [link]}) == []


# --- referrer measurement ------------------------------------------------------

def test_referrers_are_measured_once_a_week_and_within_the_cap():
    now = datetime(2026, 9, 16, tzinfo=UTC)
    readings = _readings(**{
        "0xa": {"referral": {"referred_by": "0xr1", "code": None, "referred": []}},
        "0xb": {"referral": {"referred_by": "0xr2", "code": None, "referred": []}},
        "0xc": {"referral": {"referred_by": "0xr3", "code": None, "referred": []}},
    })
    sizes = {"0xr1": {"accounts": 3, "measured_at": (now - timedelta(days=1)).isoformat()},
             "0xr2": {"accounts": 3,
                      "measured_at": (now - timedelta(days=hl_surface.REFERRER_RECHECK_DAYS)
                                      ).isoformat()}}
    assert hl_surface.referrers_to_measure(readings, sizes, now, limit=5) == ["0xr2", "0xr3"]
    assert hl_surface.referrers_to_measure(readings, sizes, now, limit=1) == ["0xr2"]


def test_a_referrer_whose_own_read_failed_is_still_measured():
    now = datetime(2026, 9, 16, tzinfo=UTC)
    readings = _readings(**{"0xa": {"referral": {"referred_by": "0xr1", "code": None,
                                                 "referred": []}}})
    readings["0xr1"] = {"subaccounts": None, "vaults": None, "referral": None}
    assert hl_surface.referrers_to_measure(readings, {}, now, limit=5) == ["0xr1"]
    readings["0xr1"]["referral"] = {"referred_by": None, "code": "X", "referred": []}
    assert hl_surface.referrers_to_measure(readings, {}, now, limit=5) == []
