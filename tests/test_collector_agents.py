# tests/test_collector_agents.py
"""The collector's record of who can act for the target: named agents, the
frontend agent, and multi-sig signers.

`userToMultiSigSigners` answers null or `{"authorizedUsers": [...],
"threshold": n}`. From 2026-09-10 the collector read it as a list, which it
never is, so a conversion to multi-sig would have been discarded silently."""

from src import collector
from src.agent_links import multisig_signers

T = "0x45d26f28196d226497130c4bac709d808fed4029"
SIGNER = "0x" + "5a" * 20


def _run(monkeypatch, answers):
    saved = {}
    monkeypatch.setattr(collector, "hl_post", lambda body: answers[body["type"]])
    monkeypatch.setattr(collector, "save_latest", lambda path, doc: saved.update(doc))
    collector.collect_agents(T)
    return saved


def test_multisig_signers_are_recorded_as_addresses_that_can_act_for_him(monkeypatch):
    saved = _run(monkeypatch, {
        "extraAgents": [],
        "userToMultiSigSigners": {"authorizedUsers": ["0x" + "5A" * 20, T], "threshold": 2},
        "webData2": {"agentAddress": None}})

    assert [(a["address"], a["source"]) for a in saved["agents"]] == [
        (SIGNER, "userToMultiSigSigners"), (T, "userToMultiSigSigners")]
    assert saved["errors"] == []


def test_an_ordinary_account_has_no_signers_and_no_error(monkeypatch):
    saved = _run(monkeypatch, {"extraAgents": [], "userToMultiSigSigners": None,
                               "webData2": {"agentAddress": None}})
    assert saved["agents"] == [] and saved["errors"] == []


def test_failed_reads_are_errors_not_an_empty_record(monkeypatch):
    """utils.hl_post answers {} for extraAgents and [] for userToMultiSigSigners
    when every retry fails — neither is an answer."""
    saved = _run(monkeypatch, {"extraAgents": {}, "userToMultiSigSigners": [],
                               "webData2": {"agentAddress": None}})
    assert saved["agents"] == []
    assert [e.split(":")[0] for e in saved["errors"]] == ["extraAgents", "userToMultiSigSigners"]


def test_multisig_signers_parser_separates_none_from_unreadable():
    assert multisig_signers(None) == []
    assert multisig_signers({"authorizedUsers": [], "threshold": 1}) == []
    assert multisig_signers([]) is None
    assert multisig_signers({}) is None
    assert multisig_signers({"authorizedUsers": ["junk", SIGNER, SIGNER]}) == [SIGNER]


# --- a new agent of HIS is news ---------------------------------------------
# An agent is an address he explicitly authorised to trade for him, which makes
# it an address he controls — the one vector strong enough to CONFIRM a wallet
# alone. The collector stored it and nothing ever diffed it: the close watch is
# the only thing that diffs agents, and the TARGET is deliberately excluded from
# the watch (a second writer on data/actions/ is a lost update). Measured
# 2026-09-22: his frontend agent went 0x98cf3fee… -> 0x6f4e393f… and no alert
# exists in the last 60 delivery shards.

NEW = "0x" + "9c" * 20
OLD = "0x" + "1d" * 20


def test_a_new_agent_is_reported():
    previous = {"agents": [{"address": OLD, "name": None, "source": "webData2"}]}
    current = {"agents": [{"address": OLD, "name": None, "source": "webData2"},
                          {"address": NEW, "name": "bot-1", "source": "extraAgents"}]}
    added = collector.new_agents(previous, current)
    assert [a["address"] for a in added] == [NEW]


def test_an_agent_that_EXPIRED_is_not_reported():
    """Only additions. An agent falling off is an expiry, and an unreadable
    endpoint also presents as a disappearance — neither is a new address."""
    previous = {"agents": [{"address": OLD, "name": None, "source": "webData2"}]}
    assert collector.new_agents(previous, {"agents": []}) == []


def test_the_first_reading_is_a_baseline_not_news():
    """The extraAgents seeding rule: announcing a three-week-old approval as a
    transition is wrong, and there is no previous record to compare against."""
    current = {"agents": [{"address": NEW, "name": None, "source": "webData2"}]}
    assert collector.new_agents(None, current) == []
    assert collector.new_agents({}, current) == []


def test_case_and_duplicates_do_not_manufacture_news():
    previous = {"agents": [{"address": NEW.upper(), "source": "webData2"}]}
    current = {"agents": [{"address": NEW, "source": "webData2"},
                          {"address": NEW, "source": "extraAgents"}]}
    assert collector.new_agents(previous, current) == []


def test_an_unreadable_webdata_is_an_error_not_an_absent_agent(monkeypatch):
    """hl_post returns {} when its retries are exhausted, and
    parse_web_data({}) answers agent_address None — indistinguishable from a
    wallet that has authorised nobody. agent_links.webdata_is_unreadable says
    so, and the caller must use it (the same fix agent_links already carries)."""
    saved = _run(monkeypatch, {"extraAgents": [], "userToMultiSigSigners": None,
                               "webData2": {}})
    assert saved["agents"] == []
    assert any("webData2" in e for e in saved["errors"]), saved["errors"]
    assert saved["sources"]["webData2"] is None, "an unreadable read is null, never a reading"
