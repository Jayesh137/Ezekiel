# src/agent_links.py
"""Linking wallets through the agents they authorise.

A Hyperliquid agent is an address an account EXPLICITLY approved to trade on its
behalf. That makes it different in kind from every other signal here: a transfer
can be a payment to a stranger, an amount match can be coincidence, and a trading
style can be imitated — but authorising an agent is a deliberate act of control
by the account owner.

So two accounts that authorise the SAME agent address are controlled by the same
person, near enough that this deserves to outrank everything else the system
measures.

The data was already being collected and used by nothing. It was also being
collected wrongly: `extraAgents` returns `[]` for an account with no agents,
which is falsy, and the collector discarded it — so "he has no agents" was
indistinguishable from "the endpoint is broken", and data/agents/ sat empty.

Measured 2026-09-10: the target and his treasury have no agents, while all three
correlation leads do. Both facts matter. The second is how a shared agent would
ever be found; the first is a baseline, and the target acquiring an agent is a
new address he controls.
"""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

AGENT_LINKS_DIR = DATA_DIR / "agent_links"


def normalise_agents(payload) -> list[dict]:
    """Agent entries out of whatever an endpoint returned.

    Accepts the raw list, so a caller can pass an API response straight in.
    """
    out = []
    for entry in payload if isinstance(payload, list) else []:
        addr = entry.get("address") if isinstance(entry, dict) else entry
        if not isinstance(addr, str):
            continue
        # Lowercase FIRST: a checksummed or shouted address still starts "0X"
        # under .upper(), and a case-sensitive prefix check silently drops it.
        addr = addr.strip().lower()
        if not addr.startswith("0x") or len(addr) != 42:
            continue
        out.append({
            "address": addr,
            "name": entry.get("name") if isinstance(entry, dict) else None,
            "valid_until": entry.get("validUntil") if isinstance(entry, dict) else None,
        })
    return out


def agent_index(by_wallet: dict) -> dict:
    """agent address -> the set of accounts that authorised it.

    `by_wallet` maps account -> list of agent entries (or raw payloads).
    """
    index: dict[str, set] = {}
    for wallet, agents in (by_wallet or {}).items():
        w = (wallet or "").lower()
        if not w:
            continue
        for agent in normalise_agents(agents):
            index.setdefault(agent["address"], set()).add(w)
    return index


def shared_agents(index: dict) -> dict:
    """Agents authorised by more than one account.

    The whole point of the module. An agent with two masters means those two
    accounts are operated by the same person.
    """
    return {agent: sorted(accounts) for agent, accounts in (index or {}).items()
            if len(accounts) > 1}


def linked_wallets(index: dict, target: str) -> dict:
    """Accounts sharing an agent with the target -> the agents they share.

    Deliberately excludes the target itself, and returns nothing when the target
    has no agents at all — which is the current state, and is a real answer
    rather than a failure.
    """
    target = (target or "").lower()
    target_agents = {a for a, accts in (index or {}).items() if target in accts}
    out: dict[str, list] = {}
    for agent in target_agents:
        for account in index[agent]:
            if account != target:
                out.setdefault(account, []).append(agent)
    return {k: sorted(v) for k, v in out.items()}


# Names the Hyperliquid UI generates, which say nothing about a person. Matching
# on these would link every mobile user to every other mobile user.
GENERIC_AGENT_NAMES = frozenset({
    "mobile qr", "trading", "apts", "agent", "api", "default", "",
})

# A template used by more accounts than this is a convention of some tool, not a
# habit of one person.
MAX_ACCOUNTS_FOR_DISTINCTIVE = 4


def name_template(name: str) -> str:
    """A name with its digits collapsed, so a SCHEME is comparable.

    `chip_oe02b` and `chip_oe06b` share the template `chip_oe#b`. One owner ran
    chip_oe02b through chip_oe05b; a different account using chip_oe06b would be
    the same person, and exact-name matching would miss it entirely.
    """
    return re.sub(r"[0-9]+", "#", (name or "").strip().lower())


def naming_families(by_wallet: dict) -> dict:
    """template -> accounts using it, for DISTINCTIVE templates only.

    Exact-name sharing turns out to be almost entirely Hyperliquid's own UI
    defaults — measured across 61 wallets, the only shared names were
    "Mobile QR" and "APTS". Matching on those would link every mobile user to
    every other one, so they are excluded, as is any template common enough to
    be a tool's convention rather than a person's habit.
    """
    by_template: dict[str, set] = {}
    for wallet, agents in (by_wallet or {}).items():
        w = (wallet or "").lower()
        for agent in normalise_agents(agents):
            name = (agent.get("name") or "").strip()
            if not name or name.lower() in GENERIC_AGENT_NAMES:
                continue
            by_template.setdefault(name_template(name), set()).add(w)
    return {tpl: sorted(accts) for tpl, accts in by_template.items()
            if 1 < len(accts) <= MAX_ACCOUNTS_FOR_DISTINCTIVE}


def build_agent_links(by_wallet: dict, target: str) -> dict:
    """The full picture: index, shared agents, and anything linked to the target."""
    index = agent_index(by_wallet)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target": (target or "").lower(),
        "wallets_checked": len(by_wallet or {}),
        "agents_seen": len(index),
        # Recorded even when zero: "we asked and there are none" is a finding,
        # and the target acquiring his first agent is a migration signal.
        "target_has_agents": any(
            (target or "").lower() in accts for accts in index.values()),
        "shared_agents": shared_agents(index),
        "linked_to_target": linked_wallets(index, target),
        # A naming SCHEME shared between accounts is a habit, where a shared
        # agent address is an act of control. Weaker, and kept separate.
        "naming_families": naming_families(by_wallet),
        "index": {a: sorted(accts) for a, accts in index.items()},
    }


def save(result: dict) -> None:
    save_latest(str(AGENT_LINKS_DIR), result)
