# Ezekiel on an always-on VM — runbook

This moves the scheduled pipeline off GitHub Actions onto one free always-on VM,
so the crons are real (not GitHub's best-effort, ~198-min median) and the repo can
go private. It replaces three stacked schedulers — `keeper.yml`, the PC dispatcher
(`scripts/dispatch_workflows.ps1`) and the Apps Script relay — with systemd
timers. Design rationale: `docs/superpowers/specs/2026-09-29-platform-migration-design.md`.

Nothing here changes detector logic. `deploy/vm/jobs.py` mirrors the workflow step
sequences (guarded by `tests/test_vm_jobs.py`), minus the `discovery_artifacts.py`
handoff, which existed only to ship the discovery SQLite between ephemeral runners
— on the VM it lives on disk at `data/.local/discovery.sqlite3` and persists.

## What stays on GitHub Actions (do NOT move these)

- `test.yml` — CI on push. Private repos get 2,000 free minutes/month; CI + the
  heartbeat use a small fraction once the scheduled jobs are on the VM.
- `heartbeat.yml` — **this is the watchdog.** It reads committed data freshness
  and alerts (ntfy) when it goes stale. Its whole value is being independent of
  the machine it watches, so it must stay on Actions. After cutover, if the VM
  dies the heartbeat notices within its window (`STALE_AFTER_MINUTES = 360`;
  consider lowering once VM cadence is proven).
- `deploy-dashboard.yml` — until the dashboard step below.
- `backfill.yml`, `substrate-backfill.yml` — manual `workflow_dispatch` only.

## One-time operator steps

1. **Create the VM.** Oracle Cloud Always Free, shape `VM.Standard.A1.Flex`
   (ARM, up to 4 OCPU / 24 GB), Ubuntu 24.04, 50–200 GB boot volume. Add your SSH
   public key at create time. If A1 capacity is unavailable in the region, use an
   always-free `VM.Standard.E2.1.Micro` (x86) — enough for polling; the graph step
   is tighter.
2. **Stop Oracle reclaiming it.** Upgrade the tenancy to Pay-As-You-Go and set a
   $1 budget alert. Always-Free shapes are still $0; this only stops idle-instance
   reclamation. (The heartbeat catches it if it ever happens anyway.)
3. **Give the VM push access.** Generate a key on the VM
   (`ssh-keygen -t ed25519`), add it as a repo **Deploy key with write access**
   (Settings → Deploy keys). This is how the VM commits `data/` back.
4. **Send me the VM's public IP** (or run the install yourself, below).

## Install (me, over SSH — or you)

```bash
sudo EZEKIEL_USER=$USER REPO_URL=git@github.com:Jayesh137/Ezekiel.git \
     bash deploy/vm/install.sh
```

Idempotent: clones to `/opt/ezekiel`, builds the venv, installs both requirement
sets, writes an empty `.env` (mode 600), generates one systemd timer per job from
`jobs.py`, and enables them. Re-run after any pull.

Then fill `/opt/ezekiel/.env` with the same secrets the repo used
(`ETHERSCAN_API_KEY`, `NTFY_TOPIC`, optional Telegram) and
`sudo systemctl restart 'ezekiel-*.timer'`. GitHub secrets cannot be read back;
reissue any that were lost (Etherscan key, ntfy topic are cheap to rotate).

## Cutover — one job at a time, no gap, no double-write

For each job in `watch, collect, trace, scan, analyze, study`:

1. Run it once by hand and confirm it writes fresh data and commits:
   `sudo -u $USER /opt/ezekiel/.venv/bin/python /opt/ezekiel/deploy/vm/run.py <job>`
2. Confirm the timer is active: `systemctl list-timers 'ezekiel-*'`.
3. Only then remove that job's `schedule:` block from its workflow (keep
   `workflow_dispatch:` so Actions stays a manual fallback), commit, push.

The VM's single global lock (`run.py`) serialises whole runs, so during the brief
window where both a VM timer and a not-yet-removed Actions cron could fire, the
`-X theirs` rebase in `commit_data` settles the race exactly as the workflows do.

After all six run cleanly on the VM for 24 h:

4. Delete `keeper.yml` and its cron. Remove the PC scheduled task:
   `schtasks /Delete /F /TN "Ezekiel workflow dispatcher"`. The Apps Script relay,
   if ever installed, can be deleted from script.google.com.
5. **Make the repo private** (Settings → Danger Zone). Verify CI + heartbeat still
   run. Deleting the 9 issues that name the target is your call (irreversible).
6. **Dashboard** (its own change): GitHub Pages won't serve a private repo on the
   free plan, and the app fetches from `raw.githubusercontent.com`. Recommended:
   a separate public repo holding only the built site with the report JSON
   encrypted (AES-GCM), decrypted client-side with a key in the URL fragment. This
   keeps the "open a link, no account" property and removes the target's address
   from anything public. Tracked as a follow-up; until it lands, keep the repo
   public or take the dashboard offline.

## Operating

- Logs: `journalctl -u 'ezekiel@*' -f`
- Force a run: `run.py <job>` as above.
- Health: the Actions heartbeat is the primary signal. Also
  `systemctl list-timers 'ezekiel-*'` and the newest `data/*/latest.json`
  `computed_at`.
- Update code: `git -C /opt/ezekiel pull` (or re-run `install.sh`). The timers pick
  up the new code on their next fire.

## Why one global lock, not two concurrency groups

Actions used `data-commit` and `watch-data` groups because many ephemeral runners
pushed to one repo. On one VM with one working tree, concurrent jobs mutating
`data/` and running git at once is the hazard, so `run.py` takes a single global
lock for the whole run. Watch may occasionally wait behind a trace run (minutes),
which is far better than the Actions median it replaces. If that latency ever
matters, the refinement is per-job git worktrees serialised only at push — not
done yet because a wrong single-writer split is the lost-update bug this repo has
already paid for once.
