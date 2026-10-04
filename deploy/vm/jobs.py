"""The scheduled jobs, defined once, for the always-on VM runner.

The GitHub workflows are the source of truth for WHICH detectors run in each job.
This mirrors their command sequences with one deliberate difference: the
`scripts/discovery_artifacts.py` restore/snapshot/cleanup steps are DROPPED. Those
exist only to ship the discovery SQLite store between ephemeral Actions runners;
on a persistent VM the store lives on disk at `data/.local/discovery.sqlite3` and
survives between runs, so the handoff is unnecessary (see the platform-migration
spec). `run.py` backs the store up nightly instead.

`tests/test_vm_jobs.py` guards against drift: every non-dropped command in each
workflow must appear here, and every command here must name a real script/module.
The per-step timeouts mirror the workflows' `timeout-minutes`; a step that exceeds
its budget is killed and the run carries on (the same "a step timeout is
survivable" invariant the workflows rely on), then the data commit persists
whatever completed.
"""

# (command, timeout_seconds). Commands are argv lists run with the repo venv's
# python; "python" is substituted for the venv interpreter by run.py.
JOBS: dict[str, dict] = {
    "collect": {
        "interval_seconds": 900,
        "steps": [
            (["python", "src/collector.py"], 540),
        ],
    },
    "watch": {
        "interval_seconds": 600,
        "steps": [
            (["python", "scripts/check_watchlist.py"], 480),
            (["python", "scripts/check_hyperevm.py"], 180),
            (["python", "scripts/check_deposit_sentinels.py"], 300),
            (["python", "scripts/check_circle_flows.py"], 240),
            (["python", "scripts/check_feed_health.py", "other"], 120),
        ],
    },
    "trace": {
        "interval_seconds": 1800,
        "steps": [
            (["python", "src/tracer.py"], 480),
            (["python", "scripts/check_bridge_destinations.py"], 180),
            (["python", "scripts/check_withdrawals.py"], 240),
            (["python", "src/correlator.py", "--pools", "cctp"], 240),
            (["python", "scripts/check_solana.py"], 180),
            (["python", "scripts/check_comovement.py"], 180),
            (["python", "scripts/check_vaults.py"], 180),
            (["python", "scripts/probe_hyperevm_index.py"], 180),
            (["python", "scripts/run_trace_engine.py"], 480),
            (["python", "src/transfer_graph.py"], 600),
            (["python", "scripts/check_identity.py"], 180),
            (["python", "scripts/check_agents.py"], 240),
            (["python", "scripts/check_hl_surface.py"], 360),
            (["python", "scripts/check_dormancy.py"], 180),
            (["python", "scripts/check_portfolio_overlap.py"], 180),
            (["python", "scripts/check_execution_program.py"], 240),
            (["python", "scripts/measure_candidates.py"], 240),
            (["python", "src/roster.py"], 180),
            (["python", "scripts/check_feed_health.py", "watch"], 120),
            (["python", "src/accounting.py"], 180),
        ],
    },
    "scan": {
        "interval_seconds": 3600,
        "steps": [
            (["python", "scripts/collect_market_discovery.py", "--once", "--max-markets", "12"], 180),
            (["python", "scripts/index_discovery_routes.py", "--resolve-sources"], 180),
            (["python", "scripts/check_newborn.py"], 300),
            (["python", "scripts/check_names.py"], 180),
            (["python", "src/scanner.py"], 1320),
            (["python", "scripts/check_successor_hypotheses.py"], 180),
            (["python", "scripts/evaluate_discovery.py", "--quality-only"], 120),
        ],
    },
    "analyze": {
        "interval_seconds": 86400,
        "requirements": "requirements-analysis.txt",
        "steps": [
            (["python", "scripts/compact_data.py", "--apply"], 300),
            (["python", "scripts/quarantine_impostor_tokens.py", "--apply"], 300),
            (["python", "scripts/reprice_transfers.py"], 300),
            (["python", "src/correlator.py"], 120),
            (["python", "scripts/resolve_first_funders.py", "40"], 180),
            (["python", "src/fingerprint.py"], 600),
            (["python", "scripts/check_gcr_hypothesis.py"], 180),
            (["python", "scripts/check_gcr_wallets.py"], 180),
            (["python", "scripts/census_execution_program.py", "--limit", "600", "--budget-seconds", "900"], 1200),
            (["python", "scripts/check_recall.py"], 180),
            (["python", "src/profile_builder.py"], 180),
            (["python", "-c", "from src.utils import update_index; update_index()"], 120),
        ],
    },
}

# Commands intentionally NOT mirrored on the VM, with why. The drift test allows
# exactly these to be absent from the VM job map.
DROPPED_ON_VM = {
    # Ephemeral-runner artifact handoff; the SQLite store is local on the VM.
    "scripts/discovery_artifacts.py",
    # pip install is done once by install.sh / the systemd unit, not per step.
    "pip",
    # backfill runs only on manual dispatch with an investigate wallet.
    "scripts/backfill_transfers.py",
    # The cron gate stops GitHub's scheduled runs evicting queued work in a
    # concurrency group; the VM runs its jobs itself and has no such queue.
    "scripts/keep_schedule.py",
}


def job_names() -> list[str]:
    return list(JOBS)
