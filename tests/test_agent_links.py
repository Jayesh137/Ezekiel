# tests/test_agent_links.py
"""Agents are the strongest ownership signal available, and were unused.

A Hyperliquid agent is an address an account EXPLICITLY approved to trade on its
behalf. A transfer can be a payment to a stranger, an amount match can be
coincidence, a trading style can be imitated — but authorising an agent is a
deliberate act of control. Two accounts sharing one are the same person.

It was collected for the target alone and read by nothing, and collected wrongly:
`extraAgents` returns `[]` for an account with no agents, which is falsy, and the
collector discarded it — so "he has none" was indistinguishable from "the
endpoint is broken" and data/agents/ sat empty.
"""

from src.agent_links import (
    agent_index,
    build_agent_links,
    linked_wallets,
    normalise_agents,
    shared_agents,
)

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
C = "0x" + "cc" * 20
AG1 = "0x" + "11" * 20
AG2 = "0x" + "22" * 20


def test_normalise_reads_the_api_shape():
    got = normalise_agents([{"address": AG1.upper(), "name": "Trading",
                             "validUntil": 1792234817379}])
    assert got == [{"address": AG1, "name": "Trading",
                    "valid_until": 1792234817379}]


def test_normalise_ignores_junk():
    assert normalise_agents(None) == []
    assert normalise_agents("nope") == []
    # "0xshort" has the prefix but is not an address length; a bare int is not
    # an address at all.
    assert normalise_agents([{"name": "no address"}, "0xshort", 7, None]) == []


def test_an_agent_with_two_masters_is_shared():
    """The finding this module exists for."""
    index = agent_index({A: [{"address": AG1}], B: [{"address": AG1}]})
    assert shared_agents(index) == {AG1: [A, B]}


def test_an_agent_with_one_master_is_not_shared():
    index = agent_index({A: [{"address": AG1}], B: [{"address": AG2}]})
    assert shared_agents(index) == {}


def test_wallets_sharing_an_agent_with_the_target_are_linked():
    index = agent_index({A: [{"address": AG1}],
                         B: [{"address": AG1}, {"address": AG2}],
                         C: [{"address": AG2}]})
    assert linked_wallets(index, target=A) == {B: [AG1]}


def test_the_target_is_never_linked_to_himself():
    index = agent_index({A: [{"address": AG1}]})
    assert linked_wallets(index, target=A) == {}


def test_a_target_with_no_agents_links_to_nobody():
    """The current live state: he has authorised none. That must produce an
    empty result, not an error and not a false link."""
    index = agent_index({A: [], B: [{"address": AG1}]})
    assert linked_wallets(index, target=A) == {}


def test_having_no_agents_is_recorded_as_a_finding():
    """`extraAgents` returns [] for an account with no agents. Discarding that
    left data/agents/ empty and indistinguishable from a broken endpoint."""
    out = build_agent_links({A: [], B: []}, target=A)
    assert out["wallets_checked"] == 2
    assert out["agents_seen"] == 0
    assert out["target_has_agents"] is False
    assert out["shared_agents"] == {}


def test_the_target_having_an_agent_is_recorded():
    out = build_agent_links({A: [{"address": AG1}]}, target=A)
    assert out["target_has_agents"] is True


def test_addresses_are_compared_case_insensitively():
    index = agent_index({A.upper(): [{"address": AG1.upper()}],
                         B: [{"address": AG1.lower()}]})
    assert shared_agents(index) == {AG1: [A, B]}


def test_build_survives_empty_input():
    out = build_agent_links({}, target=A)
    assert out["wallets_checked"] == 0
    assert out["linked_to_target"] == {}
    assert out["target_has_agents"] is False


def test_a_naming_scheme_links_accounts():
    """One owner ran chip_oe02b through chip_oe05b. A different account using
    chip_oe06b is the same person, and exact-name matching misses it."""
    from src.agent_links import naming_families
    got = naming_families({A: [{"address": AG1, "name": "chip_oe02b"}],
                           B: [{"address": AG2, "name": "chip_oe06b"}]})
    assert got == {"chip_oe#b": [A, B]}


def test_ui_default_names_are_not_evidence():
    """Measured across 61 wallets, the only shared agent names were "Mobile QR"
    and "APTS" — Hyperliquid's own UI defaults. Matching on those would link
    every mobile user to every other one."""
    from src.agent_links import naming_families
    assert naming_families({A: [{"address": AG1, "name": "Mobile QR"}],
                            B: [{"address": AG2, "name": "mobile qr"}]}) == {}


def test_a_name_used_by_one_account_is_not_a_link():
    from src.agent_links import naming_families
    assert naming_families({A: [{"address": AG1, "name": "chip_oe02b"}]}) == {}


def test_a_template_used_by_many_accounts_is_a_tool_not_a_habit():
    from src.agent_links import naming_families
    many = {f"0x{i:040x}": [{"address": f"0x{i+900:040x}", "name": f"bot{i}"}]
            for i in range(8)}
    assert naming_families(many) == {}
