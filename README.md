# Ezekiel

Tracks one Hyperliquid trader (`0x45d2…4029`, codename **Ezekiel**), builds a
behavioural fingerprint from everything they do, and tries to re-identify them if
they move to a new wallet.

The problem it solves: the owner copy-trades this wallet manually. Hyperliquid's
API only serves roughly the last 2,000 records per endpoint, so history that
isn't captured is gone for good — and if the trader migrates without being
detected, they're lost. Everything runs on free tiers.

## How it works

Three independent detection vectors feed one risk posture:

| Vector | Modules | Watches |
|---|---|---|
| Behavioural | `scanner.py`, `fingerprint.py`, `calibration.py`, `backtest.py` | Leaderboard wallets scored against the fingerprint |
| Fund flow (L1) | `tracer.py`, `correlator.py`, `linkage.py` | Arbitrum USDC, CEX-gap re-linking, address reuse |
| HL-native | `ledger_analyzer.py` | In-platform transfers that never touch L1 |

`transfer_graph.py` normalises all of them into one graph of transfer edges and
grades each discovered wallet. `risk.py` collapses everything into a 0–100 score.

Storage is JSON files committed to this repo; the dashboard reads them directly
over `raw.githubusercontent.com`, so there is no server.

## Setup (Windows / PowerShell)

Requires Python 3.12 and Node 22.

```powershell
# 1. Virtual environment (never commit it — .venv/ is gitignored)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest ruff

# 2. Verify
.\.venv\Scripts\python.exe -m pytest -q          # expect: 97 passed
.\.venv\Scripts\python.exe -m ruff check src/ tests/ scripts/   # expect: All checks passed!

# 3. Dashboard
cd dashboard
npm ci
npm run build
cd ..
```

If `python` resolves to the Microsoft Store stub, install real Python with
`winget install Python.Python.3.12` and use
`$env:LOCALAPPDATA\Programs\Python\Python312\python.exe` to create the venv.

Bash / macOS / Linux is the same with `.venv/bin/python`.

## Running things locally

All of these are read-only against public APIs except where noted. None need
credentials; without them, alerts print to stdout instead of sending.

```powershell
.\.venv\Scripts\python.exe src\collector.py       # poll HL, append to data/  (~16s)
.\.venv\Scripts\python.exe src\fingerprint.py     # rebuild profile/ + run backtest
.\.venv\Scripts\python.exe src\backtest.py        # self-match validation only
.\.venv\Scripts\python.exe src\transfer_graph.py  # rebuild the transfer graph
.\.venv\Scripts\python.exe src\risk.py            # recompute the risk score
.\.venv\Scripts\python.exe src\heartbeat.py       # is collection stale?
.\.venv\Scripts\python.exe src\tracer.py          # L1 tracing (needs ETHERSCAN_API_KEY)
.\.venv\Scripts\python.exe src\scanner.py         # leaderboard sweep (slow, ~500 wallets)
cd dashboard; npm run dev                         # dashboard at localhost:5173
```

`collector.py` and `tracer.py` **write to `data/`**. That's normal, but check
`git status` before committing so you don't mix collected data into a code commit.

## Verification checklist

Before trusting a change to scoring or matching:

1. `pytest -q` — all green.
2. `python src/backtest.py` — must PASS. It exits non-zero if the scorer can't
   rank the trader above every stranger, or if any dimension scores **0.0**
   against their own history. `None` means "excluded, too little data" and is
   fine; a hard `0.0` is always a bug — it votes against the real trader.
3. `python src/heartbeat.py` — exits non-zero when data is stale.
4. `npm run build` in `dashboard/`.

## Workflows

| Workflow | Schedule | Job |
|---|---|---|
| `collect.yml` | every 15 min | Poll HL endpoints, append data, risk score |
| `trace.yml` | every 30 min | L1 fund tracing, correlation, transfer graph |
| `scan.yml` | hourly | Leaderboard behavioural sweep |
| `analyze.yml` | daily 00:00 | Rebuild fingerprint, backtest, research profile |
| `heartbeat.yml` | every 2h | Alert if collection has stalled |
| `test.yml` | on push/PR | Ruff + pytest + dashboard build |
| `deploy-dashboard.yml` | on `dashboard/**` push | Build and publish to Pages |
| `backfill.yml` | manual | One-off historical pull |

Every job that commits data shares the `data-commit` concurrency group so pushes
serialise instead of racing on rebase.

**Collection cadence is 15 minutes, not 5.** A `*/5` cron was configured for
months but GitHub only ran it ~12–17 times a day, because each job began with a
checkout of a 749 MB data tree against a 4-minute timeout. See *Storage* below.

### Secrets

| Secret | Used by | Needed for |
|---|---|---|
| `ETHERSCAN_API_KEY` | trace, scan | Arbitrum L1 tracing (free tier) |
| `BREVO_SMTP_KEY` | all alerting jobs | Email alerts (free tier) |
| `ALERT_EMAIL` | all alerting jobs | Recipient address |

Everything degrades gracefully without them — modules log and continue.

## Storage

`data/` is **85 MB** (was 749 MB). Growth is bounded by:

- Snapshots (`positions`, `account`, `spot`, `positions_hip3_xyz`, `orders`)
  older than 7 days roll into one gzipped JSONL per day. `account` also keeps a
  small plain-JSON daily series so chart history stays directly fetchable.
- Dated scan files keep their top 25 results without fingerprints or per-dimension
  detail. Full detail lives in `scans/latest.json`; per-wallet score history in
  `data/candidates/`; the null distribution in `data/calibration/`.
- `fees` / `rate_limit` append a small summary rather than the whole API payload.

```powershell
.\.venv\Scripts\python.exe scripts\compact_data.py --dry-run   # measure
.\.venv\Scripts\python.exe scripts\compact_data.py --apply     # compact
```

**`fills`, `funding`, `ledger` and `l1_transactions` are never compacted** — they
cannot be re-fetched. The script refuses to delete an archive source until it has
read the archive back and matched the record count. Take a backup outside the
working tree before running `--apply` anyway.

## Matching: thresholds and dispositions

`src/thresholds.py` is the only place match thresholds are decided. Config
declares 0.90/0.80/0.65, but those are lowered toward the self-match ceiling from
`profile/backtest.json` — the trader only scores ~0.54 against their own adjacent
history, so an unreachable threshold would guarantee missing the migration.
Resolved values are written into `scans/latest.json` and the dashboard reads them,
so an emailed tier and a displayed tier always agree.

Precision comes from three gates instead, and every candidate gets a recorded
disposition:

- **ALERT** — promoted, emails sent.
- **WATCHLIST** — suppressed from alerting but kept with full evidence, plus the
  specific blockers (style veto, percentile gate, awaiting persistence).
- **BACKGROUND** — unremarkable.

Suppression never discards a candidate. A style-vetoed wallet cannot be promoted
on *any* route — behavioural, combined, xyz:, vault or linkage.

The percentile gate starts in **OBSERVING** mode and only enforces once
`data/calibration/population.json` holds 50+ samples, so turning it on can't
silently start dropping leads.

## Transfer graph

`data/transfer_graph/latest.json`, shown on the **/transfers** page. Wallets are
graded, never asserted:

`SERVICE` → `DIRECT_RECIPIENT` → `OPERATIONAL_COUNTERPARTY` →
`POSSIBLE_LINKED_WALLET` → `MIGRATION_CANDIDATE`

**A transfer is not ownership.** The top two tiers require corroboration from an
independent vector (behavioural similarity, amount correlation, address reuse, gas
funding, or two-way HL-native flow), so a large lone transfer can never reach
them. Exchanges and bridges are detected by config *and* by many-to-many fan
degree; they score 0.0, never alert, and are never traversed through — otherwise
every wallet on Arbitrum is three hops from the target.

Traversal is budgeted in `config.json` under `transfer_graph`: `max_depth`,
`max_nodes`, `max_expansions`, `time_budget_seconds`.

## Layout

```
src/            backend — see the module table above
  thresholds.py   single source of truth for thresholds/tiers/disposition
  transfer_graph.py  normalised transfer graph + linked-wallet grading
  heartbeat.py    alerts when collection itself stalls
dashboard/      SvelteKit 5 static SPA, deployed to GitHub Pages
data/           collected JSON — also the dashboard's API
profile/        computed fingerprint, recent fingerprint, backtest report
scripts/        compact_data.py, dedupe_fills_by_tid.py
docs/           architecture.md (see caveat below), plans, specs
```

## Known rough edges

- `docs/architecture.md` predates several changes and still describes Svelte
  components, `stores.js` and `utils.js` that were never built, plus storage
  estimates that were ~10x optimistic. Treat this README and the code as
  authoritative.
- `profile_builder.py` (research-doc ingestion, a P2 goal) writes
  `profile/trader_profile.json`, which nothing currently reads. Kept pending a
  decision.
- `data/twitter/` holds historical tweet data; the collectors were removed in
  2026-07 when free nitter bridges died. Retained, not used.
- `scripts/dedupe_fills_by_tid.py` looks like a one-off. Not reviewed.
- The repo must stay public for `raw.githubusercontent.com` and Pages to work, so
  `research/` documents are world-readable. Deliberate, but worth remembering.
