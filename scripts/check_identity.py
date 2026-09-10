#!/usr/bin/env python3
"""Ask Hyperliquid who every address of interest IS, not just what it holds.

Five identity-resolving endpoints (src/hl_identity.py) applied to the target,
his known wallets, and every roster candidate. What comes out:

  * explicit links — an address that is a cluster wallet's agent, sub-account
    or staking partner. Each is a deliberate act of control by the account
    owner, and each is strong enough to CONFIRM on its own.
  * the CURRENT frontend agent of every account, which `extraAgents` never
    lists, so the shared-agent check can actually see agents.
  * real birth dates (from the all-time value series) for the dormancy handoff.
  * presence and value on Hyperliquid for every graph wallet, so "trades on
    HL" is measured for wallets the graph found rather than only for
    leaderboard candidates.

Five free calls per address; the cluster is re-read every run, everything
else on a rota so a run stays small.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_explicit_link
from src.hl_identity import IDENTITY_DIR, explicit_links, probe, save
from src.utils import DATA_DIR, hl_post, load_config

# Addresses probed per run beyond the cluster. Five calls each.
MAX_OTHERS = 40
# Re-probe a non-cluster address this many days after its last reading.
RECHECK_DAYS = 7


def cluster(config: dict) -> list[str]:
    out = [(config.get("target_wallet") or "").lower()]
    for a in config.get("known_self_wallets", []) or []:
        a = (a or "").lower()
        if a and a not in out:
            out.append(a)
    return out


def candidates(config: dict) -> list[str]:
    """Every non-infrastructure roster wallet, strongest tier first."""
    seen = set(cluster(config))
    out = []
    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            rows = json.load(f).get("wallets", [])
    except (OSError, ValueError, AttributeError):
        rows = []
    for row in rows:
        a = (row.get("wallet") or "").lower()
        if not a or a in seen or row.get("tier") == "INFRASTRUCTURE":
            continue
        seen.add(a)
        out.append(a)
    return out


def _previous() -> dict:
    try:
        with open(IDENTITY_DIR / "latest.json") as f:
            doc = json.load(f)
        return doc.get("identities") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _stale(ident: dict, now: datetime) -> bool:
    try:
        checked = datetime.fromisoformat(ident["checked_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now - checked).days >= RECHECK_DAYS


def main() -> int:
    config = load_config()
    core = cluster(config)
    previous = _previous()
    now = datetime.now(UTC)

    identities = dict(previous)
    todo = list(core)
    others = [a for a in candidates(config) if _stale(previous.get(a, {}), now)]
    todo += others[:MAX_OTHERS]

    probed = 0
    for addr in todo:
        identities[addr] = probe(addr, hl_post, sleep=time.sleep)
        probed += 1
        time.sleep(0.2)

    links = explicit_links(identities, set(core))
    known_links = {(link["kind"], link["address"], link["linked_to"])
                   for link in (json.load(open(IDENTITY_DIR / "latest.json")).get("links") or [])
                   } if (IDENTITY_DIR / "latest.json").exists() else set()

    report = {
        "computed_at": now.isoformat(),
        "cluster": core,
        "probed_this_run": probed,
        "known": len(identities),
        "unreadable": sorted(a for a, i in identities.items() if not i.get("read_ok", False)),
        "links": links,
        "frontend_agents": {a: i.get("agent_address") for a, i in identities.items()
                            if i.get("agent_address")},
        "identities": identities,
    }
    save(report)

    print(f"[identity] probed {probed} address(es) this run, {len(identities)} on record, "
          f"{len(report['unreadable'])} unreadable")
    for a in core:
        i = identities.get(a) or {}
        print(f"[identity]   {a[:12]}... role={i.get('role')} agent={i.get('agent_address')} "
              f"staking_link={i.get('staking_link')} birth={i.get('birth_ms')}")
    if not links:
        print("[identity] no explicit links (agent / sub-account / staking) to the cluster")
    for link in links:
        key = (link["kind"], link["address"], link["linked_to"])
        tag = "NEW " if key not in known_links else ""
        print(f"[identity] {tag}LINK {link['kind']}: {link['address']} <-> "
              f"{link['linked_to']} ({link['why']})")
        if key not in known_links:
            alert_explicit_link(link["kind"], link["address"], link["linked_to"], link["why"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
