# Platform migration — design spec (sub-project 1 of 6)

**Date:** 2026-09-29
**Status:** awaiting operator review
**Scope line:** on-chain analytics of a public, pseudonymous Hyperliquid trader
for copy-trading. Public ledger data; deliverable is a Hyperliquid address, never
a real-world identity. This sub-project moves *where the existing pipeline runs*;
it changes no detector logic.

## Problem

The pipeline runs on GitHub Actions free minutes, driven by three stacked
scheduling hacks that each exist to paper over the previous one's failure:

- `keeper.yml` (self-dispatching cron chain)
- `scripts/dispatch_workflows.ps1` (Windows Task Scheduler on the operator's PC)
- `scripts/apps_script/ezekiel_relay.gs` (Google Apps Script, never installed)

Consequences, all recorded in `docs/incident-log.md`:

- **Cadence is a lie.** `trace.yml` asks for every 30 min; GitHub delivers a
  median of 198 min apart (measured over 73 runs), up to 337. A migration can go
  unseen for ~5 hours.
- **State handoff is fragile.** Because Actions runners are ephemeral, the
  discovery SQLite store is shipped between runs as a GitHub *artifact*
  (`scripts/discovery_artifacts.py`, ~250 lines of upload/download/restore/cleanup
  with 512 MiB caps, shard imports, cursor guards). Every run re-downloads and
  re-uploads it.
- **Concurrency is a minefield.** `data-commit` / `watch-data` groups, `-X theirs`
  rebase loops, single-writer-per-file rules, per-run alert-health shards — all
  exist because multiple ephemeral runners push to one repo.
- **The repo is public** because Pages + generous Actions minutes are free there.
  It exposes the target's address in issues that rank in web search.

A single always-on machine removes the *cause* of all four, not just the symptoms.

## Chosen approach

Move every scheduled job to one free always-on VM. Keep the repo as the code +
data-history store, but make it private. CI (tests on push) stays on Actions.

### VM

- **Oracle Cloud Always Free**, Ampere A1 (ARM), Ubuntu 24.04. Always-Free covers
  up to 4 OCPU / 24 GB / 200 GB block storage at $0. (Fallback if A1 capacity is
  unavailable in the region: GCP `e2-micro` always-free, x86, 1 GB — enough for
  the polling jobs, tight for the graph; or Oracle x86 micro.)
- **Operator does, once:** create the account (card required for identity, never
  charged on Always-Free), create the VM with an SSH key, upgrade to
  Pay-As-You-Go with a $1 budget alert (stops Oracle reclaiming idle Always-Free
  instances), send me the public IP. I do everything after that over SSH.

### Scheduler

- **systemd timers**, one per job, replacing the three hacks entirely. Timers log
  to the journal, run on wake, and never silently drop an occurrence.
- Intervals matched to what the job needs, not GitHub's best-effort cron:
  watch 10 min, collect 15 min, trace 30 min, scan 60 min, analyze daily. These
  become *real* now, so the 198-min blind spot closes.
- `flock` per concurrency group so two jobs never run concurrently — replaces the
  Actions concurrency groups. Because there is now exactly **one writer**, the
  `-X theirs` rebase dance and per-run alert shards are no longer load-bearing
  (kept as-is for now; simplified in a later cleanup, not this sub-project).

### State

- The discovery SQLite store lives on the VM's disk permanently. The GitHub-
  artifact handoff (`discovery_artifacts.py`) is **no longer needed** for runtime;
  a nightly compressed snapshot is committed to the repo (or rsynced) purely as
  backup. This deletes the single most complex and failure-prone part of the
  current design from the hot path.
- `data/*/latest.json` and history still commit to the repo, so the dashboard and
  git history are unchanged in shape.

### Secrets

- One `.env` on the VM (mode 600), loaded by systemd `EnvironmentFile`. Operator
  pastes the values once (Etherscan key, NTFY topic, optional Telegram). These are
  the same secrets already in GitHub; if any are lost, they're cheap to reissue.
  No secret is ever committed.

### Repo goes private

- Safe once scheduled jobs are off Actions: remaining Actions usage is CI on push
  + the watchdog + dashboard build, far under the 2,000 free private minutes/mo.
- Delete the workflow files that moved to the VM (keeper, collect, trace, scan,
  watch, analyze, backfill, heartbeat, substrate-backfill). Keep `test.yml`.
- Deleting the 9 existing issues that name the target is a separate operator
  call (irreversible); recommended but not required by this sub-project.

### Dashboard (the one real sub-decision)

GitHub Pages does not serve from a private repo on the Free plan, and the app
currently fetches data from `raw.githubusercontent.com` (also private-blocked).
Three options:

- **A — separate public "dashboard" repo, encrypted data (recommended).** The VM
  writes the report JSON encrypted (AES-GCM) into a small public repo that holds
  only the built static site — no addresses in source. The phone app decrypts
  client-side with a key in the URL fragment (never sent to any server). Free,
  no new account, target data not publicly readable. Cost: ~1 day to add the
  encrypt-on-write + decrypt-on-load and strip hard-coded addresses from source.
- **B — Cloudflare Pages + Access.** Free tier deploys from a private repo;
  Cloudflare Access gates the site to your email (free ≤50 users). No client-side
  crypto. Cost: one Cloudflare account (operator effort), and data lands on
  Cloudflare.
- **C — serve from the VM behind a free tunnel** (Cloudflare Tunnel /
  Tailscale). Most private, but adds a moving part and a login step on the phone.

Recommendation: **A**. It keeps the "no account, no server, open a link" property
the phone app was built around, and it removes the target's address from anything
public.

### Watchdog

- A tiny hourly `watchdog.yml` on Actions reads `data/*/latest.json`'s
  `computed_at` from the (now private) repo via the Actions token; if the VM has
  gone quiet past a threshold, it sends ntfy. This is the one thing that must
  *not* live on the VM, because its job is to notice the VM dying.

## Migration order (no gap, no double-write)

1. Stand up the VM; install Python, clone repo (deploy key), `.env`, run one job
   manually, verify output matches an Actions run.
2. Install systemd timers **disabled**; dry-run each once by hand.
3. Cut over one job at a time: enable the VM timer, then remove that job's cron
   from Actions (leave `workflow_dispatch`). Watch for one healthy cycle each.
   `flock` + single machine means no overlap with the retiring Actions cron
   during the brief window both could fire.
4. Once all jobs run on the VM for 24 h clean: make the repo private, add the
   watchdog, switch the dashboard to option A.
5. Decommission the three schedulers (delete workflows; operator removes the PC
   scheduled task via the documented `schtasks /Delete`).

## Testing

- Existing suite (1,873 Python + 52 dashboard) must stay green; tests remain
  network-free and must not touch real `data/`.
- New: a VM smoke script that runs each entrypoint with `--dry-run` where
  available and asserts each writes its `latest.json`.
- New: dashboard decrypt round-trip test (option A) — encrypt a fixture, load it,
  assert the app renders it; assert no address literal remains in built source.
- Verify the watchdog fires on a deliberately stale fixture and stays quiet on a
  fresh one.

## Explicitly out of scope here

Detector logic, the fingerprint/census/tape work (sub-projects 2-3), and
simplifying the now-redundant concurrency machinery (a later cleanup, once the
single-writer invariant has held in production for a while).

## Risks

- **Oracle reclaims the instance.** Mitigated by Pay-As-You-Go upgrade + $1 budget
  alert; the watchdog catches it if it still happens.
- **A1 capacity unavailable at create time.** Fallback shapes above; the design
  doesn't depend on ARM.
- **VM is a single point of failure.** GitHub crons stay as `workflow_dispatch`
  (not deleted, just un-scheduled) so the operator or watchdog can fall back to
  Actions manually. The watchdog is the tripwire.
