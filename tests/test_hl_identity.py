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
