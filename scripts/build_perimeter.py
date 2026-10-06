#!/usr/bin/env python3
"""Build his perimeter (data/perimeter/latest.json) and close the loop on Hyperliquid.

    python scripts/build_perimeter.py                # the trace.yml step
    python scripts/build_perimeter.py --dry-run DIR  # read-only: writes only DIR, no alerts

Every role comes from a file another detector wrote (spec §4 of
docs/superpowers/specs/2026-10-06-boundary-trace-design.md). The substrate pass
that finds associates and exchange families is the slow part, so it runs once a
day and its result is carried in the file between runs. Single writer:
data/perimeter/.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import utils  # noqa: E402

CLOSE_PER_RUN = 25
CLOSE_EVERY_S = 24 * 3600
SUBSTRATE_EVERY_S = 24 * 3600
ACTIVE_VALUE_USD = 10_000.0
ACTIVE_VOLUME_USD = 100_000.0


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _age_s(stamp, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(stamp))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def hl_reading(address: str, post) -> dict:
    """Three strict reads. A failure is unreadable, never an empty account (rule 5)."""
    checked = datetime.now(UTC).isoformat()
    try:
        state = post({"type": "clearinghouseState", "user": address}) or {}
        post({"type": "spotClearinghouseState", "user": address})
        portfolio = post({"type": "portfolio", "user": address}) or []
    except Exception as exc:  # noqa: BLE001 - transport or budget, reported
        return {"checked_at": checked, "read_ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    value = float((state.get("marginSummary") or {}).get("accountValue") or 0)
    volume, peak, birth = 0.0, 0.0, None
    for name, body in portfolio if isinstance(portfolio, list) else []:
        if name != "allTime":
            continue
        volume = float((body or {}).get("vlm") or 0)
        points = [(t, float(v)) for t, v in (body or {}).get("accountValueHistory") or []
                  if float(v) != 0]
        if points:
            peak = max(v for _, v in points)
            birth = datetime.fromtimestamp(points[0][0] / 1000, tz=UTC).date().isoformat()
    active = (value >= ACTIVE_VALUE_USD or peak >= ACTIVE_VALUE_USD
              or volume >= ACTIVE_VOLUME_USD)
    return {"checked_at": checked, "read_ok": True, "account_value": value,
            "all_time_volume": volume, "max_value": peak, "birth": birth, "active": active}


def default_post():
    from scripts.run_trace_engine import paced_post
    from src.hl_budget import ReadBudget
    return paced_post(ReadBudget(seconds=120, weight_per_minute=400))


def main(argv=None, *, post=None, substrate=None, now=None, is_hot=None,
         close_every_s: float = CLOSE_EVERY_S) -> int:
    from src.boundary import perimeter as pm
    from src.boundary.measure import load_services, measured
    from src.trace import store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR")
    args = parser.parse_args(argv)
    config = utils.load_config()
    data = utils.DATA_DIR
    now_dt = datetime.fromisoformat(now) if now else datetime.now(UTC)
    now_iso = now_dt.isoformat()
    out_dir = Path(args.dry_run) if args.dry_run else data / "perimeter"
    previous = _read(data / "perimeter" / "latest.json")

    sentinels = _read(data / "deposit_sentinels" / "latest.json").get("sentinels") or {}
    trace_report = _read(data / "trace" / "latest.json")
    notes = []
    try:
        registry = store.load(data / "trace" / "registry")
    except RuntimeError as exc:
        registry, notes = {}, [f"trace registry unreadable: {exc}"]
    solana = _read(data / "labels" / "solana_addresses.json").get("addresses") or {}
    services, hot = load_services(config, data)
    contract, busy, _known = measured(data)
    hot_test = is_hot or (lambda a: a in hot or busy(a) or contract(a))
    core = {(config.get("target_wallet") or "").lower()} | {
        (w or "").lower() for w in config.get("known_self_wallets") or []}
    core.discard("")
    deposits = set(sentinels) | {(r.get("address") or "").lower()
                                 for r in trace_report.get("deposit_addresses") or []}
    deposits.discard("")

    if not previous or _age_s(previous.get("substrate_at"), now_dt) >= SUBSTRATE_EVERY_S:
        if substrate is None:
            from src.chain.collect import records_by_wallet as substrate
        rows = substrate(sorted(core | deposits))
        records = [r for a in sorted(rows) for r in rows[a]]
        found = pm.associates([r for a in sorted(core) for r in rows.get(a, [])], core,
                              is_contract=contract, is_busy=busy, services=services)
        families = pm.exchange_families(records, deposits, core, is_hot=hot_test)
        substrate_at = now_iso
    else:
        found = previous.get("associates") or {}
        families = previous.get("exchange_families") or {}
        substrate_at = previous.get("substrate_at")

    doc = pm.build(config=config, sentinels=sentinels, trace_report=trace_report,
                   trace_registry=registry, solana=solana, associates_found=found,
                   families=families, services=services, previous=previous, now_iso=now_iso)
    doc.update(associates=found, substrate_at=substrate_at, notes=notes,
               alerted=list(previous.get("alerted") or []))

    post = post or default_post()
    due = sorted((m for m in doc["members"].values()
                  if m["role"] != "core" and str(m["address"]).startswith("0x")
                  and _age_s((m.get("hl") or {}).get("checked_at"), now_dt) >= close_every_s),
                 key=lambda m: str((m.get("hl") or {}).get("checked_at") or ""))
    closed = {"checked": 0, "unreadable": [], "active": []}
    for m in due[:CLOSE_PER_RUN]:
        m["hl"] = hl_reading(m["address"], post)
        closed["checked"] += 1
        if not m["hl"]["read_ok"]:
            closed["unreadable"].append(m["address"])
    closed["active"] = sorted(a for a, m in doc["members"].items()
                              if m["role"] != "core" and (m.get("hl") or {}).get("active"))
    doc["closed"] = closed

    if not args.dry_run:
        from src.alerts import alert_perimeter_hl_account
        for addr in closed["active"]:
            m = doc["members"][addr]
            if addr in doc["alerted"] or m["weight"] < 0.6:
                continue
            if alert_perimeter_hl_account(m, m["hl"]):
                doc["alerted"].append(addr)
    out_dir.mkdir(parents=True, exist_ok=True)
    utils.atomic_write_json(out_dir / "latest.json", doc)
    print(f"[perimeter] {len(doc['members'])} members {doc['counts']}; HL checked "
          f"{closed['checked']} ({len(closed['unreadable'])} unreadable), "
          f"{len(closed['active'])} active non-core")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
