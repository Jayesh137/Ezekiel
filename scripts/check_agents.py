#!/usr/bin/env python3
"""Ask every wallet we care about which agents it has authorised.

A Hyperliquid agent is an address an account EXPLICITLY approved to trade on its
behalf. Two accounts authorising the same agent are operated by the same person —
a deliberate act of control, not a coincidence of flow or of style. That makes it
the strongest single signal this system can produce, and it was being collected
for the target alone and used by nothing.

Free: one `extraAgents` call per wallet, no key, no Etherscan budget.

Two findings come out of it, and the second only exists because the first is
recorded even when empty:

  * A SHARED agent — two accounts, one agent — means common control.
  * The target acquiring his first agent is a new address he controls, and a
    plausible precursor to moving accounts. He had none on 2026-09-10, so that
    baseline has to be on disk for the change to be visible.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent_links import (
    AGENT_LINKS_DIR,
    build_agent_links,
    normalise_agents,
    save,
    webdata_is_unreadable,
)
from src.alerts import alert_shared_agent, alert_target_gained_agent
from src.utils import DATA_DIR, hl_post, load_config

# One call each. The roster is a few hundred wallets at most, and infrastructure
# is skipped, so this stays small.
DEFAULT_MAX_WALLETS = 120


def wallets_to_check(config: dict) -> list[str]:
    """The target, anything believed to be his, and every roster candidate.

    Infrastructure is skipped: an exchange's agents say nothing about him.

    The cap is applied HERE rather than by the caller, so that the operator's
    watch list survives it. A shared agent is the one vector strong enough to
    CONFIRM alone, and until 2026-09-12 the wallet under close watch was cut by
    this cap — its named agent `0x1e8695b7…` was never in the index at all,
    while `linked_to_target: {}` read as a measured no.
    """
    from src.roster import detector_candidates

    target = (config.get("target_wallet") or "").lower()
    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            roster = json.load(f)
    except (OSError, ValueError, AttributeError):
        roster = {}
    # The target is never his own candidate inside the selector, so he is put
    # back at the head: this detector reads HIS agents to compare against.
    return [target] + detector_candidates(config, roster, DEFAULT_MAX_WALLETS - 1)


def _previous() -> dict:
    try:
        with open(AGENT_LINKS_DIR / "latest.json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main() -> int:
    config = load_config()
    target = (config.get("target_wallet") or "").lower()
    wallets = wallets_to_check(config)

    from src.hl_identity import parse_web_data

    by_wallet: dict[str, list] = {}
    unreadable = 0
    for wallet in wallets:
        try:
            resp = hl_post({"type": "extraAgents", "user": wallet})
        except Exception as exc:                      # noqa: BLE001 - transport
            unreadable += 1
            print(f"[agents] {wallet[:12]}... unreadable: {type(exc).__name__}: {exc}")
            continue
        # `[]` is a real answer meaning "authorised none", and is recorded as
        # such. A failed call is NOT recorded, so it can never read as "none".
        if isinstance(resp, list):
            by_wallet[wallet] = normalise_agents(resp)
        else:
            unreadable += 1
            time.sleep(0.15)
            continue
        # The frontend agent that actually signs orders is NOT in extraAgents;
        # only webData2 reports it. Two accounts driven by the same one are the
        # same browser session, which is the same person.
        #
        # A failed call here used to vanish: hl_post returns `{}` once its
        # retries are exhausted, parse_web_data reads that as "no agent", and
        # nothing was counted. Same guard as extraAgents above — a read we
        # could not make must never serialise as a wallet that authorised
        # nobody.
        try:
            payload = hl_post({"type": "webData2", "user": wallet})
        except Exception as exc:                      # noqa: BLE001 - transport
            unreadable += 1
            print(f"[agents] {wallet[:12]}... webData2 unreadable: {type(exc).__name__}")
            payload = None
        if webdata_is_unreadable(payload):
            if payload is not None:
                unreadable += 1
                print(f"[agents] {wallet[:12]}... webData2 unreadable: empty response")
        else:
            web = parse_web_data(payload)
            if web["agent_address"]:
                by_wallet[wallet].append({"address": web["agent_address"],
                                          "name": None,
                                          "validUntil": web["agent_valid_until"]})
        time.sleep(0.15)

    result = build_agent_links(by_wallet, target)
    result["unreadable"] = unreadable
    previous = _previous()
    save(result)

    print(f"[agents] checked {len(by_wallet)} wallet(s), "
          f"{result['agents_seen']} agent(s) seen, {unreadable} unreadable")

    shared = result["shared_agents"]
    if shared:
        for agent, accounts in shared.items():
            print(f"[agents] SHARED AGENT {agent} <- {', '.join(accounts)}")
            alert_shared_agent(agent, accounts)
    else:
        print("[agents] no shared agents — no two accounts authorise the same one")

    # A new agent for the target is a new address he controls.
    had = set()
    for agent, accounts in (previous.get("index") or {}).items():
        if target in accounts:
            had.add(agent)
    now = {a for a, accts in result["index"].items() if target in accts}
    for agent in sorted(now - had):
        name = next((x.get("name") for x in by_wallet.get(target, [])
                     if x.get("address") == agent), None)
        print(f"[agents] TARGET AUTHORISED A NEW AGENT: {agent} ({name})")
        alert_target_gained_agent(agent, name)
    if not now:
        print("[agents] target has authorised no agents (checked, not assumed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
