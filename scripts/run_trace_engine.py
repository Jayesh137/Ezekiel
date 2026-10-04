# scripts/run_trace_engine.py
"""Run the trace engine: follow his money until it reaches Hyperliquid.

    python scripts/run_trace_engine.py               # the trace.yml step
    python scripts/run_trace_engine.py --dry-run DIR # read-only: state and report
                                                     # go to DIR, no sweeps, no alerts

Spec: docs/superpowers/specs/2026-10-04-trace-engine-design.md. The engine's own
budgets bound it (DEFAULTS["seconds"] = 240 < the step's timeout-minutes: 8).
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, load_config, save_latest  # noqa: E402

TRACE_DIR = DATA_DIR / "trace"


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def paced_post(read_budget):
    """cctp_feed.strict_post, paced by response weight (hl_budget.ReadBudget).

    Strict: a failure RAISES, so the ledger reader can never mistake an outage
    for an empty ledger (CLAUDE.md: never read a cursor feed through hl_post).
    """
    from src.cctp_feed import strict_post

    def post(body):
        if not read_budget.before(body):
            raise RuntimeError(f"hl read budget stopped: {read_budget.stopped_reason}")
        data = strict_post(body, timeout=30, retries=3)
        read_budget.after(body, data)
        return data
    return post


def services_and_hot(config: dict) -> tuple[set, set]:
    from src.chain.labels import SERVICE_CATEGORIES, load_registry

    registry = load_registry(DATA_DIR / "labels" / "entities.json")
    services = {a for a, e in registry.items()
                if e.get("category") in SERVICE_CATEGORIES - {"cex_deposit", "cex_deposit_sweep"}}
    services |= {(a or "").lower() for a in config.get("known_service_addresses") or []}
    services |= {(a or "").lower() for a in config.get("excluded_addresses") or []}
    services.add((config.get("hl_bridge_contract") or "").lower())
    hot = {a for a, e in registry.items() if e.get("category") == "cex_hot"}
    return services - {""}, hot


def make_sweep(config: dict):
    from src.chain.budget import CallBudget
    from src.chain.chains import enabled_chains
    from src.chain.collect import sweep_wallet
    from src.chain.spam import ground_truth_addresses
    from src.transfer_graph import _canonical, _par_contracts, lookup_call_budget

    chains = enabled_chains(config)
    plan_refused: dict = {}

    def sweep(address):
        return sweep_wallet(address, chains,
                            CallBudget(max_calls=lookup_call_budget(chains), seconds=45),
                            canonical=_canonical(config), cluster=False,
                            plan_refused=plan_refused,
                            protected=ground_truth_addresses(config),
                            par_contracts=_par_contracts())
    return sweep


def alert_new(report: dict) -> int:
    """HIGH for an HL account newly reached by his money; the sentinel alert
    (CRITICAL when measured quiet) for a new sender into a HyperCore deposit
    address of his. Everything else is recorded, never buzzed."""
    from src.alerts import alert_deposit_address_shared, alert_trace_reached

    sent = 0
    for row in report.get("new_reached") or []:
        sent += bool(alert_trace_reached(row))
    for link in report.get("new_links") or []:
        if link.get("kind") != "shared_hl_deposit":
            continue
        sent += bool(alert_deposit_address_shared({
            "address": link["wallet"], "sentinel": link["via"],
            "usd": link.get("outsider_usd") or 0, "count": 1, "assets": [],
            "chains": ["hyperliquid"], "last_ts": link.get("ts"),
            # A HyperCore sender is an HL account whose own ledger the engine
            # read only if it was reached; without a whole-venue reading it is
            # never claimed quiet, so this alerts HIGH, not CRITICAL.
            "class": "unmeasured"}))
    return sent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", metavar="DIR", help="read-only run writing only into DIR")
    parser.add_argument("--seconds", type=float, default=None)
    args = parser.parse_args(argv)

    from src.chain.activity import ActivityCache
    from src.chain.collect import records_by_wallet
    from src.hl_budget import ReadBudget
    from src.trace import engine, store

    config = load_config()
    cluster = {(config.get("target_wallet") or "").lower()}
    cluster |= {(w or "").lower() for w in config.get("known_self_wallets") or []}
    services, hot = services_and_hot(config)
    inferred = set(_read(DATA_DIR / "labels" / "inferred_deposits.json").get("addresses") or {})
    # What the project already measured: his deposit sentinels and the graph's
    # conduits are deposit addresses; roster INFRASTRUCTURE is measured busy or
    # contract. Re-deriving them would spend budget to learn known facts.
    from src.deposit_sentinels import forwarding_candidates
    inferred |= set(_read(DATA_DIR / "deposit_sentinels" / "latest.json").get("sentinels") or {})
    inferred |= set(forwarding_candidates(
        _read(DATA_DIR / "transfer_graph" / "latest.json").get("nodes") or [], None))
    services |= {(w.get("wallet") or "").lower()
                 for w in _read(DATA_DIR / "roster" / "latest.json").get("wallets") or []
                 if w.get("tier") == "INFRASTRUCTURE"}
    inferred -= cluster
    services -= cluster
    code = _read(DATA_DIR / "labels" / "code_cache.json")
    from src.linkage import swept_wallets
    already_swept = swept_wallets(config)

    out_dir = Path(args.dry_run) if args.dry_run else TRACE_DIR
    activity_path = DATA_DIR / "labels" / "address_activity.json"
    if args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        if TRACE_DIR.exists() and not (out_dir / "registry").exists():
            shutil.copytree(TRACE_DIR, out_dir, dirs_exist_ok=True)
        shutil.copy(activity_path, out_dir / "address_activity.json")
        activity_path = out_dir / "address_activity.json"

    registry = store.load(out_dir / "registry")
    hl_store = store.load(out_dir / "hl_edges")
    previous = _read(out_dir / "latest.json")
    budgets = dict(engine.DEFAULTS)
    if args.seconds:
        budgets["seconds"] = args.seconds
    if args.dry_run:
        budgets["l1_sweeps"] = 0

    activity = ActivityCache(activity_path, max_lookups=budgets["classify_reads"],
                             seconds=min(90.0, budgets["seconds"] / 2))
    # Parse the substrate BEFORE the engine's clock starts: the first full read
    # is the slow one (files are then cached in-process by collect._load_cached),
    # and the engine's budget is for units, not for warming a cache.
    t0 = time.monotonic()
    records_by_wallet(sorted(cluster))
    print(f"[trace] substrate loaded in {time.monotonic() - t0:.0f}s")
    started = time.monotonic()
    with ReadBudget(seconds=budgets["seconds"], weight_per_minute=600) as hl_budget:
        report = engine.run(
            cluster=cluster, registry=registry, hl_store=hl_store, previous=previous,
            l1_records_for=lambda addrs: records_by_wallet(addrs),
            hl_post=paced_post(hl_budget), activity=activity,
            sweep=(lambda a: {}) if args.dry_run else make_sweep(config),
            services=services, inferred=inferred, cex_hot=hot, code=code,
            already_swept=already_swept, budgets=budgets)
        report["hl_budget"] = hl_budget.report()

    written = store.save(out_dir / "registry", registry)
    written += store.save(out_dir / "hl_edges", hl_store)
    report["shards_written"] = len(written)
    if args.dry_run:
        (out_dir / "latest.json").write_text(json.dumps(report, indent=2))
    else:
        save_latest(str(TRACE_DIR), report)
        report["alerts_sent"] = alert_new(report)

    u = report["units"]
    print(f"[trace] {time.monotonic() - started:.0f}s: hl {u['hl']}, classify {u['classify']}, "
          f"l1 {u['l1']} units; {report['addresses_known']} addresses, {report['edges']} edges")
    print(f"[trace] HL accounts reached: {len(report['reached_hl_accounts'])} "
          f"({len(report['new_reached'])} new); links {len(report['links'])} "
          f"({len(report['new_links'])} new); deposit addresses {len(report['deposit_addresses'])}; "
          f"funders {len(report['funders'])}; errors {len(report['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
