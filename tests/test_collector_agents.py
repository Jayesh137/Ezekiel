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
