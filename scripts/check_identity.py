#!/usr/bin/env python3
"""Ask Hyperliquid who every address of interest IS, not just what it holds.

Five identity-resolving endpoints (src/hl_identity.py) applied to the target,
his known wallets, every roster candidate and the transfer graph's nodes and
conduits.
What comes out:

  * explicit links — an address that is a cluster wallet's agent, sub-account
    or staking partner. Each is a deliberate act of control by the account
    owner, and each is strong enough to CONFIRM on its own.
  * the CURRENT frontend agent of every account, which `extraAgents` never
    lists, so the shared-agent check can actually see agents.
  * real birth dates (from the all-time value series) for the dormancy handoff.
  * presence, value and 30-day volume on Hyperliquid for the roster's wallets
    and the graph's nodes and conduits, so "trades on HL" is measured for the
    wallets the target funded and for the ones the graph's conduit pass would
    otherwise write off as infrastructure.

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


def load_graph() -> dict | None:
    """The stored transfer graph document, or None with one logged line.

    A missing, unparseable or non-object file gives None: the sweep carries on without
    the graph's wallets and never raises over them. The file is ~35 MB and parses in over
    a second, so `candidates` reads it once and hands it to both readers below.
    """
    path = DATA_DIR / "transfer_graph" / "latest.json"
    try:
        with open(path) as f:
            doc = json.load(f)
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        print(f"[identity] could not read the transfer graph {path.parent.name}/{path.name}: {exc}")
        return None
    return doc


def graph_nodes(graph: dict | None = None) -> list[str]:
    """Wallets of the transfer graph's `nodes`, lower-cased, in the file's order.

    The graph sorts its nodes most promising first (classification rank, a migration
    candidate before an operational counterparty before a service, then confidence), so
    the file's order is kept. A row that is not an object or has no string `wallet` is
    skipped. An unreadable graph, or one with no `nodes` list, gives [] and one logged
    line. Pass the document `load_graph` returned to avoid parsing the file again.
    """
    if graph is None:
        graph = load_graph()
        if graph is None:
            return []
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        print("[identity] the transfer graph has no `nodes` list; no nodes queued")
        return []
    out = []
    for row in nodes:
        wallet = row.get("wallet") if isinstance(row, dict) else None
        if isinstance(wallet, str) and wallet.strip():
            out.append(wallet.strip().lower())
    return out


def graph_conduits(graph: dict | None = None) -> list[str]:
    """Addresses the transfer graph classified as conduits, lower-cased and sorted.

    The graph's `services` map names every address it will not follow and why, and a
    conduit's reason starts "conduit: forwards ...". An unreadable graph, or one with no
    `services` map, gives [] and one logged line. Pass the document `load_graph`
    returned to avoid parsing the file again.
    """
    if graph is None:
        graph = load_graph()
        if graph is None:
            return []
    services = graph.get("services")
    if not isinstance(services, dict):
        print("[identity] the transfer graph has no `services` map; no conduits queued")
        return []
    return sorted({address.lower() for address, reason in services.items()
                   if isinstance(address, str) and isinstance(reason, str)
                   and reason.lower().startswith("conduit")})


def candidates(config: dict) -> list[str]:
    """Who the sweep reads after the cluster, in the order it reads them.

    1. The roster's leads (CONFIRMED, PROBABLE, POSSIBLE), in roster order.
    2. The transfer graph's nodes not already listed, in the graph's order, whatever the
       roster says of them. Whether a wallet the target funded now trades on Hyperliquid
       is `transfer_graph`'s `trades_on_hl`, and it can only be judged on an address this
       sweep has read (measured 2026-10-07: 182 of the graph's 299 nodes had never been
       probed).
    3. The graph's conduits not already listed, likewise whatever the roster says of
       them. The roster files a conduit as INFRASTRUCTURE or does not hold it at all, yet
       a conduit that trades on Hyperliquid is the migration the conduit pass's exemption
       exists to keep, and that exemption sees only an address this sweep has read
       (measured 2026-10-07: 9 of 145 conduits traded $196.5M in 30 days).
    4. Every other non-INFRASTRUCTURE roster wallet, in roster order.

    The cluster is never listed: it is read on every run. An address qualifying under
    several headings is listed once, at the first. Only the order is decided here:
    `main` skips what a fresh reading already covers and probes `MAX_OTHERS` a run.
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
    graph = load_graph()
    if graph is not None:
        for address in graph_nodes(graph):
            queue(address)
        for address in graph_conduits(graph):
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
    again on the next pass (candidates come the roster's leads first, then the transfer
    graph's nodes and conduits, then the other roster rows; MAX_OTHERS a run) rather than
    up to a week later. The KEY marks a probe that has run since: one whose
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
