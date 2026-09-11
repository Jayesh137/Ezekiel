# tests/test_check_watchlist.py
"""The close watch must ask every endpoint that can name an agent.

A Hyperliquid account can hold an agent in two places and they are not the
same field. `webData2.agentAddress` is the UNNAMED frontend agent that signs
orders from the web UI; `extraAgents` is the list of NAMED agents, which is
what an API wallet appears in. The watch read the first and the explorer's
`approveAgent` actions, and never asked for the second.

Measured 2026-09-12 against the live API, on the one wallet under watch:

    0xdd53c529…  agentAddress=None
                 extraAgents=[{'name': 'agent-2026-08-17',
                               'address': '0x1e8695b7…',
                               'validUntil': 1796627644729}]

So `data/watchlist/latest.json` serialised `"agents": []` for a wallet that
has one. The explorer could not cover for it either: that window is the last
300 actions, unpageable, and on a wallet trading this hard the `approveAgent`
from its birth rolled out of it long ago — it is not in `data/actions/` at all.

That matters more here than anywhere else. A shared agent is the one vector in
this project strong enough to CONFIRM alone, and the watch's `new_agent` change
was dead for named agents on exactly the wallets busy enough to be worth
watching.
"""

import scripts.check_watchlist as check

W = "0xdd53c5297309130ab5fe5623dc905752e3342b13"
AGENT = "0x1e8695b7261ff0b422ccf46f0ccf093fad308c3a"
FRONTEND = "0x98cf3fee01cb61905a79b63c4d7662cc638c72e2"


def _stub(monkeypatch, *, extra_agents, frontend_agent=None):
    """Everything read_wallet touches, answered from memory. No network."""
    asked = []

    def fake_hl_post(body):
        asked.append(body.get("type"))
        if body.get("type") == "extraAgents":
            if isinstance(extra_agents, Exception):
                raise extra_agents
            return extra_agents
        if body.get("type") == "spotClearinghouseState":
            return {"balances": []}
        if body.get("type") == "userFills":
            return []
        if body.get("type") == "subAccounts":
            return []
        return {}

    monkeypatch.setattr(check, "hl_post", fake_hl_post)
    monkeypatch.setattr(check, "probe", lambda addr, fetch, **kw: {
        "read_ok": True, "errors": [], "role": "user", "master": None,
        "owner": None, "agent_address": frontend_agent, "leading_vaults": [],
        "staking_link": None})
    monkeypatch.setattr(check, "live_hip3_dexes", lambda: [])
    monkeypatch.setattr(check, "merged_clearinghouse_state", lambda addr, **kw: {
        "marginSummary": {"accountValue": "53616202.18"}, "assetPositions": []})
    monkeypatch.setattr(check, "fetch_actions", lambda addr: ([], None))
    monkeypatch.setattr(check, "own_actions", lambda rows, addr: [])
    monkeypatch.setattr(check, "record", lambda addr, acts: None)
    monkeypatch.setattr(check, "account_activity", lambda addr: {"nonce": 0,
                                                                 "errors": []})
    monkeypatch.setattr(check, "records_for", lambda addr: [])
    return asked


def test_a_named_extra_agent_is_seen_by_the_watch(monkeypatch):
    asked = _stub(monkeypatch, extra_agents=[
        {"name": "agent-2026-08-17", "address": AGENT.upper(),
         "validUntil": 1796627644729}])

    snap, _counterparties = check.read_wallet(W, {})

    assert "extraAgents" in asked, "the watch never asked for named agents"
    assert snap["agents"] == [AGENT]


def test_the_frontend_agent_and_a_named_agent_are_both_kept(monkeypatch):
    _stub(monkeypatch,
          extra_agents=[{"name": "agent-2026-08-17", "address": AGENT}],
          frontend_agent=FRONTEND)

    snap, _ = check.read_wallet(W, {})

    assert snap["agents"] == sorted([AGENT, FRONTEND])


def test_an_unreadable_agent_endpoint_never_serialises_as_no_agents(monkeypatch):
    """Rule 5: 'we could not tell' must not look like 'there is nothing there'."""
    _stub(monkeypatch, extra_agents=RuntimeError("503"))

    snap, _ = check.read_wallet(W, {})

    assert snap["agents"] == []
    assert snap["read_ok"] is False
    assert any("extraAgents" in e for e in snap["errors"])


def test_an_account_with_no_agents_reads_clean(monkeypatch):
    """The target's own answer. An empty list is a real 'none', not an outage."""
    _stub(monkeypatch, extra_agents=[])

    snap, _ = check.read_wallet(W, {})

    assert snap["agents"] == []
    assert snap["read_ok"] is True
    assert snap["errors"] == []
