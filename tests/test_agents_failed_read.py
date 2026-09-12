# tests/test_agents_failed_read.py
"""A webData2 read that FAILED must never be recorded as "no frontend agent".

`utils.hl_post` returns a sentinel when every retry is exhausted — `[]` for a
user-typed request and `{}` otherwise — which is the right shape for a caller
treating empty as "nothing here" and the wrong one here. `webData2` is the only
endpoint that reports the frontend agent, the one that actually signs orders,
and two accounts driven by the same one are the same browser session: the
CONFIRM-alone vector. A successful read always returns a populated document, so
`{}` from this endpoint is only ever a failure — and `parse_web_data({})`
answers `agent_address: None`, which is indistinguishable from a real "he has
authorised nobody".

`extraAgents` was already guarded this way; its sibling call was not, and
`unreadable` stayed 0 while the answer was silently wrong. Observed live
2026-09-12 during a run that logged a read timeout on api.hyperliquid.xyz.

This is rule 5, on the strongest vector the project has.
"""


from src import agent_links
from src.hl_identity import parse_web_data


def test_parse_web_data_cannot_tell_a_failure_from_an_empty_account():
    """The premise: why the caller, not the parser, has to make the distinction."""
    assert parse_web_data({})["agent_address"] is None
    assert parse_web_data({"agentAddress": None})["agent_address"] is None


def test_an_empty_webdata2_payload_is_a_failed_read():
    assert agent_links.webdata_is_unreadable({}) is True
    assert agent_links.webdata_is_unreadable(None) is True
    assert agent_links.webdata_is_unreadable([]) is True


def test_a_populated_payload_with_no_agent_is_a_real_answer():
    """He has authorised nobody — a finding, and it must be recorded as one."""
    assert agent_links.webdata_is_unreadable(
        {"clearinghouseState": {"marginSummary": {"accountValue": "1.0"}},
         "agentAddress": None}) is False


def test_a_failed_webdata2_read_counts_as_unreadable(monkeypatch, tmp_path):
    calls = _run_check_agents(monkeypatch, tmp_path,
                             extra_agents=[], web_data={})
    assert calls["unreadable"] == 1
    assert calls["result"]["index"] == {}


def test_a_successful_read_with_no_agent_is_not_unreadable(monkeypatch, tmp_path):
    calls = _run_check_agents(
        monkeypatch, tmp_path, extra_agents=[],
        web_data={"clearinghouseState": {}, "agentAddress": None})
    assert calls["unreadable"] == 0


def test_the_frontend_agent_is_still_indexed_when_the_read_succeeds(monkeypatch, tmp_path):
    agent = "0x98cf3fee01cb61905a79b63c4d7662cc638c72e2"
    calls = _run_check_agents(
        monkeypatch, tmp_path, extra_agents=[],
        web_data={"clearinghouseState": {}, "agentAddress": agent})
    assert agent in calls["result"]["index"]
    assert calls["unreadable"] == 0


def _run_check_agents(monkeypatch, tmp_path, *, extra_agents, web_data):
    """check_agents.main() against a one-wallet roster and canned responses."""
    import json

    import scripts.check_agents as ca

    target = "0x45d26f28196d226497130c4bac709d808fed4029"
    monkeypatch.setattr(ca, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ca, "AGENT_LINKS_DIR", tmp_path / "agent_links")
    monkeypatch.setattr(ca, "load_config", lambda: {"target_wallet": target,
                                                    "known_self_wallets": []})
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": []}))

    def fake_post(body):
        return list(extra_agents) if body["type"] == "extraAgents" else dict(web_data)

    monkeypatch.setattr(ca, "hl_post", fake_post)
    monkeypatch.setattr(ca.time, "sleep", lambda *_: None)
    saved = {}
    monkeypatch.setattr(ca, "save", lambda r: saved.update(result=r))
    for name in dir(ca):
        if name.startswith("alert_"):
            monkeypatch.setattr(ca, name, lambda *a, **k: True)

    ca.main()
    return {"result": saved["result"], "unreadable": saved["result"]["unreadable"]}
