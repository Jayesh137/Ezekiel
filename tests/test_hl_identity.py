# tests/test_hl_identity.py
"""Identity-resolving Hyperliquid endpoints, parsed offline.

Shapes were captured from the live API on 2026-09-10: the target's frontend
agent resolves back to him through userRole, `extraAgents` never lists it,
and `portfolio`'s all-time series gives an account's real birth.
"""

from src import hl_identity as hi

T = "0x45d26f28196d226497130c4bac709d808fed4029"
AGENT = "0x98cf3fee01cb61905a79b63c4d7662cc638c72e2"


def test_role_parsing_resolves_agents_and_subaccounts():
    assert hi.parse_role({"role": "user"}) == {"role": "user", "master": None, "owner": None}
    assert hi.parse_role({"role": "agent", "data": {"user": T.upper()}})["owner"] == T
    assert hi.parse_role({"role": "subAccount", "data": {"master": "0xABC"}})["master"] == "0xabc"
    assert hi.parse_role({"role": "missing"})["role"] == "missing"
    assert hi.parse_role("garbage")["role"] is None


def test_web_data_yields_the_frontend_agent_extraagents_hides():
    got = hi.parse_web_data({
        "agentAddress": AGENT.upper(), "agentValidUntil": 1789595431182,
        "leadingVaults": [], "isVault": False,
        "clearinghouseState": {"marginSummary": {"accountValue": "59873355.83"}}})
    assert got["agent_address"] == AGENT
    assert got["agent_valid_until"] == 1789595431182
    assert got["account_value"] == 59873355.83
    assert got["leading_vaults"] == [] and got["is_vault"] is False


def test_web_data_tolerates_a_missing_or_malformed_agent():
    assert hi.parse_web_data({"agentAddress": None})["agent_address"] is None
    assert hi.parse_web_data({"agentAddress": "0x12"})["agent_address"] is None
    assert hi.parse_web_data(None)["agent_address"] is None


def test_staking_link_is_a_pair_or_nothing():
    assert hi.parse_staking_link({"stakingLink": None}) is None
    assert hi.parse_staking_link({"stakingLink": {"stakingUser": "0xA", "tradingUser": "0xB"}}) \
        == {"staking_user": "0xa", "trading_user": "0xb"}
    assert hi.parse_staking_link({"stakingLink": {"stakingUser": "0xA"}}) is None


def test_delegations_sum_per_validator():
    got = hi.parse_delegations([
        {"validator": "0xV1", "amount": "41446.5"},
        {"validator": "0xv1", "amount": "1.5"},
        {"validator": "0xV2", "amount": "62177.2"},
        {"validator": "0xV3", "amount": "bad"},
    ])
    assert got == {"0xv1": 41448.0, "0xv2": 62177.2}


def test_birth_is_the_first_nonzero_alltime_point_never_zero():
    payload = [["day", {"accountValueHistory": [[5, "1.0"]]}],
               ["allTime", {"accountValueHistory": [[1704321900064, "0.0"],
                                                    [1709233340241, "10000.0"]]}]]
    assert hi.parse_birth(payload) == 1709233340241
    assert hi.parse_birth([["allTime", {"accountValueHistory": [[1, "0.0"]]}]]) is None
    assert hi.parse_birth("nope") is None


def test_birth_skips_a_point_that_overflows_a_float():
    """float(10**400) and int(float("inf")) raise OverflowError, which was not caught."""
    huge, inf = 10**400, float("inf")
    payload = [["allTime", {"accountValueHistory": [[1704321900064, huge], [inf, "5.0"],
                                                    [1709233340241, "10000.0"]]}]]
    assert hi.parse_birth(payload) == 1709233340241
    assert hi.parse_birth([["allTime", {"accountValueHistory": [[1, huge], [inf, "5.0"]]}]]) is None


UNKNOWN_ACTIVITY = {"total_value": None, "month_volume": None}


def test_activity_is_the_newest_total_value_across_windows_and_the_month_volume():
    """Measured 2026-10-07: a lead with $0 perp margin held $9.37M in spot USDC."""
    payload = [
        ["day", {"accountValueHistory": [[3000, "11.0"], [4000, "12.0"]], "vlm": "1.0"}],
        ["week", {"accountValueHistory": [[2000, "9.0"]], "vlm": "2.0"}],
        ["month", {"accountValueHistory": [[1000, "8.0"]], "vlm": "80039479.12"}],
        ["allTime", {"accountValueHistory": [[5000, "9370000.5"]], "vlm": "99.0"}],
        # The perp-only windows are never read, however new their points are.
        ["perpDay", {"accountValueHistory": [[9000, "0.0"]], "vlm": "7.0"}],
        ["perpMonth", {"accountValueHistory": [[9500, "3.0"]], "vlm": "8.0"}],
        ["perpAllTime", {"accountValueHistory": [[9900, "4.0"]], "vlm": "9.0"}],
    ]
    assert hi.parse_activity(payload) == {"total_value": 9370000.5, "month_volume": 80039479.12}


def test_the_newest_point_wins_whichever_window_or_position_holds_it():
    """Newest by timestamp: not the largest value, not one window, not the last listed."""
    in_the_day_window = [["allTime", {"accountValueHistory": [[5000, "2.5"]]}],
                         ["day", {"accountValueHistory": [[6000, "1.5"], [1000, "9.0"]]}]]
    assert hi.parse_activity(in_the_day_window)["total_value"] == 1.5
    in_the_all_time_window = [["day", {"accountValueHistory": [[6000, "1.5"]]}],
                              ["allTime", {"accountValueHistory": [[7000, "0.5"], [1000, "8.0"]]}]]
    assert hi.parse_activity(in_the_all_time_window)["total_value"] == 0.5


def test_a_month_volume_of_zero_is_a_reading_and_a_missing_one_is_not():
    got = hi.parse_activity([["month", {"accountValueHistory": [], "vlm": "0.0"}]])
    assert got == {"total_value": None, "month_volume": 0.0}
    assert got["month_volume"] is not None
    # No vlm field, an unparseable one, and a window that is not the last 30 days.
    assert hi.parse_activity([["month", {"accountValueHistory": [[1, "5.0"]]}]]) == {
        "total_value": 5.0, "month_volume": None}
    assert hi.parse_activity([["month", {"vlm": "oops"}]])["month_volume"] is None
    assert hi.parse_activity([["allTime", {"vlm": "9.0"}], ["perpMonth", {"vlm": "9.0"}]]
                             )["month_volume"] is None


def test_activity_is_unknown_not_zero_when_the_payload_does_not_say():
    malformed_points = [[None, "1"], [1], "p", {"a": 1}, [2, "bad"], [3, None]]
    for payload in ({}, None, "nope", [], [["day", "x"]], ["day"], [["day", {}]],
                    [["day", {"accountValueHistory": malformed_points}]]):
        assert hi.parse_activity(payload) == UNKNOWN_ACTIVITY
    # A malformed entry is skipped like parse_birth skips one; the good ones still read.
    mixed = [["day", "x"], ["week", {"accountValueHistory": malformed_points + [[8, "3.0"]]}]]
    assert hi.parse_activity(mixed) == {"total_value": 3.0, "month_volume": None}


def test_a_value_that_is_not_a_finite_number_is_not_a_reading():
    """float("nan") parses, and json.dump would write a bare NaN that JSON.parse rejects."""
    for junk in ("nan", "inf", "-inf", "1e999", float("nan"), float("inf"), True):
        payload = [["month", {"accountValueHistory": [[9, junk]], "vlm": junk}]]
        assert hi.parse_activity(payload) == UNKNOWN_ACTIVITY
    # An older valid point still reads when the newest one is not a number.
    older = [["day", {"accountValueHistory": [[1, "4.0"], [9, "nan"]]}]]
    assert hi.parse_activity(older)["total_value"] == 4.0


def test_a_number_too_large_for_a_float_is_no_reading_and_never_raises():
    """float(10**400) raises OverflowError, which escaped parse_activity and so probe."""
    huge = 10**400
    payload = [["month", {"accountValueHistory": [[9, huge]], "vlm": huge}]]
    assert hi.parse_activity(payload) == UNKNOWN_ACTIVITY
    # Skipped like any malformed point: the older readable one still reads, as does a point
    # whose timestamp is infinite (int(float("inf")) overflows too).
    older = [["day", {"accountValueHistory": [[1, "4.0"], [9, huge], [float("inf"), "7.0"]]}]]
    assert hi.parse_activity(older)["total_value"] == 4.0


def _fake_fetch(answers, failing=()):
    def fetch(body):
        kind = body["type"]
        if kind in failing:
            raise RuntimeError("boom")
        return answers.get(kind)
    return fetch


def test_probe_asks_every_endpoint_and_names_failures():
    answers = {
        "userRole": {"role": "user"},
        "webData2": {"agentAddress": AGENT, "agentValidUntil": 1, "leadingVaults": [],
                     "isVault": False, "clearinghouseState": {"marginSummary": {"accountValue": "5"}}},
        "userFees": {"stakingLink": None},
        "delegations": [{"validator": "0xV", "amount": "3"}],
        "portfolio": [["allTime", {"accountValueHistory": [[7, "1"]]}]],
    }
    got = hi.probe(T, _fake_fetch(answers))
    assert got["read_ok"] is True and got["errors"] == []
    assert got["role"] == "user" and got["agent_address"] == AGENT
    assert got["delegations"] == {"0xv": 3.0} and got["birth_ms"] == 7
    assert hi.present(got) is True

    partial = hi.probe(T, _fake_fetch(answers, failing={"portfolio"}))
    assert partial["read_ok"] is False
    assert partial["errors"] == ["portfolio: RuntimeError: boom"]
    assert partial["role"] == "user"            # the other answers survive
    assert hi.present(partial) is None          # a failed read is not an answer


def test_probe_stores_the_total_value_and_month_volume_and_a_failed_read_leaves_none():
    answers = {
        "userRole": {"role": "user"},
        # webData2's accountValue is perp margin only: a spot account reads 0 here.
        "webData2": {"clearinghouseState": {"marginSummary": {"accountValue": "0.0"}}},
        "userFees": {"stakingLink": None},
        "delegations": [],
        "portfolio": [["day", {"accountValueHistory": [[7, "9370000.5"]], "vlm": "1.0"}],
                      ["month", {"accountValueHistory": [[6, "9.0"]], "vlm": "80039479.12"}],
                      ["allTime", {"accountValueHistory": [[3, "0.0"], [4, "3.0"]]}]],
    }
    got = hi.probe(T, _fake_fetch(answers))
    assert got["account_value"] == 0.0
    assert got["total_value"] == 9370000.5 and got["month_volume"] == 80039479.12
    assert got["birth_ms"] == 4                 # the existing reading is untouched

    failed = hi.probe(T, _fake_fetch(answers, failing={"portfolio"}))
    assert failed["read_ok"] is False
    assert failed["total_value"] is None and failed["month_volume"] is None
    assert failed["account_value"] == 0.0       # the other answers survive

    # utils.hl_post answers {} for a failed portfolio read: still unknown, never zero.
    blank = hi.probe(T, _fake_fetch({**answers, "portfolio": {}}))
    assert blank["total_value"] is None and blank["month_volume"] is None


def test_probe_survives_a_portfolio_answer_that_overflows_a_float():
    huge = 10**400
    portfolio = [["month", {"accountValueHistory": [[9, huge]], "vlm": huge}],
                 ["allTime", {"accountValueHistory": [[9, huge]]}]]
    got = hi.probe(T, _fake_fetch({"portfolio": portfolio}))     # must not raise
    assert got["total_value"] is None and got["month_volume"] is None and got["birth_ms"] is None


def test_presence_distinguishes_missing_from_unread():
    assert hi.present({"read_ok": True, "role": "missing"}) is False
    assert hi.present({"read_ok": True, "role": "vault"}) is True
    assert hi.present({"read_ok": True, "role": None}) is None
    assert hi.present({}) is None


def test_explicit_links_name_agents_subaccounts_and_staking_pairs():
    cluster = {T}
    identities = {
        AGENT: {"owner": T},                                  # his agent
        "0xsub": {"master": T},                               # his sub-account
        "0xcold": {"staking_link": {"staking_user": "0xcold", "trading_user": T}},
        "0xother": {"owner": "0xsomeoneelse"},                # unrelated agent
        T: {"owner": None, "master": None, "staking_link": None},
    }
    links = hi.explicit_links(identities, cluster)
    kinds = {(link["kind"], link["address"], link["linked_to"]) for link in links}
    assert kinds == {("agent", AGENT, T), ("subaccount", "0xsub", T),
                     ("staking_link", "0xcold", T)}


def test_a_cluster_wallet_that_is_someones_agent_names_that_someone():
    links = hi.explicit_links({T: {"owner": "0xboss"}}, {T})
    assert links == [{"kind": "agent", "address": T, "linked_to": "0xboss",
                      "why": "authorised as an agent by this account"}]
