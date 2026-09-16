#!/usr/bin/env python3
"""Watch his private exchange deposit addresses for senders outside the cluster.

See `src/deposit_sentinels.py` for why this exists and what a finding is worth.

Runs in watch.yml, the fast job. Costs: one incremental sweep per sentinel
(cursors make a repeat nearly free), a few whole-chain activity readings for
addresses not yet measured, and two Hyperliquid reads per NEW sender only.

Single-writer rules this respects: the shared activity cache
(`data/labels/address_activity.json`) belongs to trace.yml's graph step and is
only READ here; readings this script takes are kept in its own report.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import deposit_sentinels as ds
from src import utils
from src.utils import load_config

CHAINS = ("arbitrum", "ethereum", "base", "optimism", "polygon")
# Live readings per run and a wall-clock bound on them. One reading is two
# HTTP calls at a 30s timeout; nesting: this < the step's timeout in watch.yml.
MAX_LIVE_READINGS = 8
READING_SECONDS = 45.0


def _load(path: Path) -> dict:
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def cluster(config: dict) -> set:
    """Config only — ground truth. A roster tier is an inference."""
    out = {(config.get("target_wallet") or "").lower()}
    out |= {(w or "").lower() for w in config.get("known_self_wallets") or []}
    out.discard("")
    return out


class Readings:
    """Per-chain activity readings: shared cache (read-only), then our own
    stored readings, then a bounded number of live ones kept in our report."""

    def __init__(self, stored: dict, fetch=None, *, max_live=MAX_LIVE_READINGS,
                 seconds=READING_SECONDS, clock=time.monotonic):
        from src.chain.activity import ActivityCache, fetch_activity
        try:
            self._shared = ActivityCache(utils.DATA_DIR / "labels" / "address_activity.json",
                                         max_lookups=0)
        except Exception:                             # noqa: BLE001
            self._shared = None
        self.own = {a: dict(v) for a, v in (stored or {}).items() if isinstance(v, dict)}
        self._fetch = fetch or fetch_activity
        self._left = max_live
        self._deadline = clock() + seconds
        self._clock = clock
        self.live = 0

    def for_address(self, address: str, chains) -> list:
        a = (address or "").lower()
        out = []
        for chain in CHAINS:
            shared = self._shared.cached(a, chain) if self._shared else None
            mine = (self.own.get(a) or {}).get(chain)
            reading = shared or mine
            if reading is None and chain in (chains or ()) and self._left > 0 \
                    and self._clock() < self._deadline:
                self._left -= 1
                self.live += 1
                got = self._fetch(a, chain)
                if got is not None:
                    reading = {**got, "checked_at": datetime.now(UTC).isoformat()}
                    self.own.setdefault(a, {})[chain] = reading
            if reading is not None:
                out.append(reading)
        return out


def hl_state(address: str, post=None) -> dict:
    """Two strict reads: never a timeout serialised as an empty account (rule 5)."""
    from src.cctp_feed import strict_post
    post = post or (lambda body: strict_post(body, timeout=30, retries=2))
    try:
        state = post({"type": "clearinghouseState", "user": address}) or {}
        role = (post({"type": "userRole", "user": address}) or {}).get("role")
        portfolio = post({"type": "portfolio", "user": address}) or []
        birth = None
        for name, body in portfolio:
            if name == "allTime":
                points = [p for p in (body or {}).get("accountValueHistory") or []
                          if float(p[1]) != 0]
                if points:
                    birth = datetime.fromtimestamp(points[0][0] / 1000, tz=UTC).date().isoformat()
        fills = post({"type": "userFills", "user": address}) or []
        return {"read_ok": True, "role": role, "birth": birth, "fills": len(fills),
                "account_value": (state.get("marginSummary") or {}).get("accountValue")}
    except Exception as exc:                          # noqa: BLE001 - transport
        return {"read_ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    from scripts.check_watchlist import sweep
    from src.alerts import alert_deposit_address_shared
    from src.chain.collect import records_by_wallet

    config = load_config()
    members = cluster(config)
    path = ds.sentinel_dir() / "latest.json"
    previous = _load(path) or None
    readings = Readings((previous or {}).get("readings") or {})
    now = datetime.now(UTC).isoformat()

    graph = _load(utils.DATA_DIR / "transfer_graph" / "latest.json")
    inferred = _load(utils.DATA_DIR / "labels" / "inferred_deposits.json")
    candidates = ds.forwarding_candidates(graph.get("nodes") or [], inferred)
    kept = set((previous or {}).get("sentinels") or {})
    configured = [(a or "").lower() for a in config.get("sentinel_addresses") or []]

    cluster_rows = records_by_wallet(sorted(members))
    paid = ds.cluster_payments([r for rows in cluster_rows.values() for r in rows], members)
    classes = {}
    for addr in set(candidates) | kept | set(configured):
        if (paid.get(addr) or {}).get("usd", 0.0) < ds.MIN_CLUSTER_USD \
                and addr not in kept and addr not in configured:
            continue
        chains = (paid.get(addr) or {}).get("chains") or ()
        classes[addr] = ds.classify(readings.for_address(addr, chains))
    sentinels, pending, excluded = ds.select_sentinels(
        candidates, paid, classes, previous, configured)
    for entry in sentinels.values():
        entry["since"] = entry.get("since") or now

    plan_refused: dict = {}
    for addr in sorted(sentinels):
        sweep(addr, config, plan_refused)

    rows = records_by_wallet(sorted(sentinels))
    found, unvalued = {}, {}
    for addr in sentinels:
        found[addr], unvalued[addr] = ds.senders(rows.get(addr) or [], addr, members)
    for senders in found.values():
        for sender, e in senders.items():
            classes[sender] = ds.classify(readings.for_address(sender, e["chains"]))
    sharers = ds.sharers(found, classes)

    live_keys = {ds.sharer_key(r) for r in sharers}
    retry = [r for r in (previous or {}).get("undelivered_alerts") or []
             if ds.sharer_key(r) in live_keys]
    pending_alerts, seen = [], set()
    for row in retry + ds.news(previous, sharers):
        key = ds.sharer_key(row)
        if key in seen:
            continue
        seen.add(key)
        current = next((r for r in sharers if ds.sharer_key(r) == key), row)
        state = hl_state(current["address"])
        print(f"[sentinels] NEW SENDER {current['address']} -> {current['sentinel'][:12]}... "
              f"${current['usd']:,.0f} ({current['class']}); HL {state}")
        if not alert_deposit_address_shared(current, state):
            pending_alerts.append(current)

    report = {
        "computed_at": now,
        "cluster": sorted(members),
        "sentinels": sentinels,
        "pending": pending,
        "excluded": excluded,
        "sharers": sharers,
        "unvalued_senders": {a: sorted(s) for a, s in unvalued.items() if s},
        "known_senders": ds.known_senders(previous, found, pending_alerts),
        "undelivered_alerts": pending_alerts,
        "readings": readings.own,
        "live_readings_taken": readings.live,
    }
    ds.save(report)
    print(f"[sentinels] {len(sentinels)} sentinel(s), {len(pending)} awaiting a reading, "
          f"{len(excluded)} excluded as shared; {len(sharers)} outside sender(s) on record, "
          f"{len(seen)} alerted this run, {len(pending_alerts)} undelivered")
    for row in sharers:
        print(f"[sentinels]   {row['address']} paid {row['sentinel'][:12]}... "
              f"${row['usd']:,.0f} ({row['class']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
