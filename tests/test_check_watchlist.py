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


# --- the target's own size, for comparison -------------------------------------
#
# A watched wallet is only interesting relative to the account it is a
# candidate for, and nothing read both. Measured 2026-09-11: the watched wallet
# $53.2M, the target $24.1M across perp, xyz and spot.

T = "0x45d26f28196d226497130c4bac709d808fed4029"


def test_the_targets_value_is_read_the_same_way_the_watched_wallet_is(monkeypatch):
    """Apples to apples, or the ratio is meaningless.

    `data/account/latest.json` holds only the CONFIGURED dexes (xyz) and is
    written by a best-effort cron, so comparing it against a watched wallet
    read across every LIVE dex would divide two different quantities and could
    manufacture a crossing out of a stale file.
    """
    seen = {}

    def fake_merged(addr, **kw):
        seen[addr] = kw.get("dexes")
        return {"marginSummary": {"accountValue": "17357742.18"}, "assetPositions": []}

    monkeypatch.setattr(check, "merged_clearinghouse_state", fake_merged)
    monkeypatch.setattr(check, "live_hip3_dexes", lambda: ["xyz", "flx"])
    monkeypatch.setattr(check, "hl_post", lambda body: {
        "balances": [{"coin": "USDC", "total": "6747796.94"}]})

    value = check.target_account_value({"target_wallet": T.upper()})

    assert seen[T] == ["xyz", "flx"], "the target was not read across the live dexes"
    assert value == 17357742.18 + 6747796.94


def test_an_unreadable_target_is_none_not_zero(monkeypatch):
    """Rule 6. A target priced at 0.0 makes every watched wallet infinitely
    larger than him, which would fire the crossing on an outage."""
    def boom(addr, **kw):
        raise RuntimeError("503")

    monkeypatch.setattr(check, "merged_clearinghouse_state", boom)
    monkeypatch.setattr(check, "live_hip3_dexes", lambda: [])

    assert check.target_account_value({"target_wallet": T}) is None


def test_the_reading_carries_the_targets_value_and_the_ratio(monkeypatch):
    _stub(monkeypatch, extra_agents=[])

    snap, _ = check.read_wallet(W, {}, target_value=24_105_539.0)

    assert snap["target_value"] == 24_105_539.0
    assert snap["size_ratio"] == round(53616202.18 / 24_105_539.0, 4)


def test_a_reading_with_no_target_value_still_records_the_wallet(monkeypatch):
    """The watch must not stop working because his own account was unreadable."""
    _stub(monkeypatch, extra_agents=[])

    snap, _ = check.read_wallet(W, {})

    assert snap["account_value"] == 53616202.18
    assert snap["target_value"] is None and snap["size_ratio"] is None
    assert snap["read_ok"] is True


def test_the_target_is_priced_once_a_run_not_once_a_wallet(monkeypatch):
    """Every watched wallet is compared against the SAME reading of him.

    Pricing him per wallet would spend a call set each time and, worse, compare
    wallets against different moments of a book that moved 42% in a day.
    """
    calls, given = [], []
    second = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"

    def fake_target_value(config):
        calls.append(config)
        return 24_105_539.0

    def fake_read_wallet(address, config, target_value=None):
        given.append((address, target_value))
        return check.snapshot(address, account_value=1.0,
                              target_value=target_value), []

    monkeypatch.setattr(check, "load_config", lambda: {
        "target_wallet": T, "watch_wallets": [W, second]})
    monkeypatch.setattr(check, "_roster", lambda: {})
    monkeypatch.setattr(check, "target_account_value", fake_target_value)
    monkeypatch.setattr(check, "read_wallet", fake_read_wallet)
    monkeypatch.setattr(check, "sweep", lambda addr, cfg: None)
    monkeypatch.setattr(check, "target_world", lambda cfg: {})
    monkeypatch.setattr(check, "_previous", lambda: {})
    monkeypatch.setattr(check, "busy_flags", lambda hits: {})
    monkeypatch.setattr(check, "save", lambda report: None)

    assert check.main() == 0
    assert len(calls) == 1, "the target was priced more than once"
    assert given == [(W, 24_105_539.0), (second, 24_105_539.0)]


def test_a_cctp_withdrawal_counts_as_a_withdrawal_destination(monkeypatch):
    """`sendToEvmWithData` is the native Circle withdrawal. The ledger shows
    it only as a send to 0x2000…0000; the explorer payload names the real
    recipient, and the watch must treat that recipient exactly as it treats a
    `withdraw3` destination — as a counterparty and a withdrawal."""
    _stub(monkeypatch, extra_agents=[])
    recipient = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
    monkeypatch.setattr(check, "own_actions", lambda rows, addr: [
        {"hash": "0x1", "time": 1, "type": "sendToEvmWithData", "destination": recipient,
         "destination_chain": "base", "destination_domain": 6, "amount": "2500000",
         "token": "USDC", "error": None, "action": {}}])

    snap, counterparties = check.read_wallet(W, {})

    assert snap["withdrawal_destinations"] == [recipient]
    assert recipient in counterparties


def test_the_roster_feeds_the_watch_and_a_broken_one_never_shrinks_it(monkeypatch, tmp_path):
    """A CONFIRMED wallet of his must be read per-wallet without anyone editing
    config.json — `0xf078969e…` sat CONFIRMED at 0.84 with nothing watching it.
    And an unreadable roster must leave the operator's own list intact."""
    from src import utils

    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    assert check._roster() == {}                      # no file: not a crash

    (tmp_path / "roster").mkdir()
    (tmp_path / "roster" / "latest.json").write_text(
        '{"wallets": [{"wallet": "0xF078969E55CABF9AE3F26AFEB5EC627B4430F19E",'
        ' "tier": "CONFIRMED", "confidence": 0.84, "is_service": false,'
        ' "reasons": ["Sends to the same deposit address as the target"]}]}')
    seen = check.watched({"target_wallet": T, "watch_wallets": [W]},
                         roster=check._roster())
    addrs = [x["address"] for x in seen]
    assert addrs == [W, "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"]
    assert seen[1]["source"] == "roster" and "CONFIRMED" in seen[1]["why"]

    (tmp_path / "roster" / "latest.json").write_text("{ not json")
    assert check._roster() == {}
