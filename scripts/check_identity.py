#!/usr/bin/env python3
"""Ask Hyperliquid who every address of interest IS, not just what it holds.

Five identity-resolving endpoints (src/hl_identity.py) applied to the target,
his known wallets, every roster candidate and the transfer graph's conduits.
What comes out:

  * explicit links — an address that is a cluster wallet's agent, sub-account
    or staking partner. Each is a deliberate act of control by the account
    owner, and each is strong enough to CONFIRM on its own.
  * the CURRENT frontend agent of every account, which `extraAgents` never
    lists, so the shared-agent check can actually see agents.
  * real birth dates (from the all-time value series) for the dormancy handoff.
  * presence, value and 30-day volume on Hyperliquid for the roster's wallets
    and the graph's conduits, so "trades on HL" is measured for the wallets the
    graph's conduit pass would otherwise write off as infrastructure.

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
from src.roster import TIER_CONFIRMED, TIER_INFRASTRUCTURE, TIER_POSSIBLE, TIER_PROBABLE
from src.utils import DATA_DIR, hl_post, load_config

# The roster's lead tiers: read before anything else outside the cluster.
LEAD_TIERS = (TIER_CONFIRMED, TIER_PROBABLE, TIER_POSSIBLE)
# Addresses probed per run beyond the cluster. Five calls each, and measured
# at 109s for 42 addresses — the largest single step in the trace job, which
# has a hard timeout and loses its commit if it is cancelled. Fifteen a run
# against a half-hourly schedule still re-reads every roster wallet many
# times over inside the seven-day recheck window; the cluster is read on
# every run regardless, which is where a new agent or sub-account appears.
MAX_OTHERS = 15
# Re-probe a non-cluster address this many days after its last reading, or at once
# when its row predates the `total_value` field (see _stale).
RECHECK_DAYS = 7


def cluster(config: dict) -> list[str]:
    out = [(config.get("target_wallet") or "").lower()]
    for a in config.get("known_self_wallets", []) or []:
        a = (a or "").lower()
        if a and a not in out:
            out.append(a)
    return out


def graph_conduits() -> list[str]:
    """Addresses the transfer graph classified as conduits, lower-cased and sorted.

    The graph's `services` map names every address it will not follow and why, and a
    conduit's reason starts "conduit: forwards ...". A missing, unparseable or oddly
    shaped file gives [] and one line saying so: the sweep carries on without the
    conduits, and never raises over them.
    """
    path = DATA_DIR / "transfer_graph" / "latest.json"
    try:
        with open(path) as f:
            services = json.load(f).get("services")
        if not isinstance(services, dict):
            raise ValueError("no `services` map")
    except (OSError, ValueError, AttributeError) as exc:
        print(f"[identity] could not read the graph's conduits from {path.name}: {exc}")
        return []
    return sorted({address.lower() for address, reason in services.items()
                   if isinstance(address, str) and isinstance(reason, str)
                   and reason.lower().startswith("conduit")})


def candidates(config: dict) -> list[str]:
    """Who the sweep reads after the cluster, in the order it reads them.

    1. The roster's leads (CONFIRMED, PROBABLE, POSSIBLE), in roster order.
    2. The transfer graph's conduits not already listed, whatever the roster says of
       them. The roster files a conduit as INFRASTRUCTURE or does not hold it at all,
       yet a conduit that trades on Hyperliquid is the migration the conduit pass's
       exemption exists to keep, and that exemption sees only an address this sweep
       has read (measured 2026-10-07: 9 of 145 conduits traded $196.1M in 30 days).
    3. Every other non-INFRASTRUCTURE roster wallet, in roster order.

    The cluster is never listed: it is read on every run.
    """
    seen = set(cluster(config))
    out: list[str] = []

    def queue(wallet) -> None:
        a = (wallet or "").lower()
        if a and a not in seen:
            seen.add(a)
            out.append(a)

    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            rows = json.load(f).get("wallets", [])
    except (OSError, ValueError, AttributeError):
        rows = []
    for row in rows:
        if row.get("tier") in LEAD_TIERS:
            queue(row.get("wallet"))
    for address in graph_conduits():
        queue(address)
    for row in rows:
        if row.get("tier") != TIER_INFRASTRUCTURE:
            queue(row.get("wallet"))
    return out


def _previous() -> dict:
    try:
        with open(IDENTITY_DIR / "latest.json") as f:
            doc = json.load(f)
        return doc.get("identities") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _stale(ident: dict, now: datetime) -> bool:
    """Due a re-read: the reading is RECHECK_DAYS old, or it predates the portfolio fields.

    `total_value` arrived with `parse_activity`. A row probed before that carries only
    perp margin, which reads 0 for a wallet holding its money in spot, so it is read
    again on the next pass (candidates come strongest tier first, MAX_OTHERS a run)
    rather than up to a week later. The KEY marks a probe that has run since: one whose
    portfolio read failed stored None under it, and is not read again early.
    """
    if not isinstance(ident, dict) or "total_value" not in ident:
        return True
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
