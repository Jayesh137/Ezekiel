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
.\.venv\Scripts\python.exe -m pytest -q          # expect: 369 passed
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

**Collection cadence is 15 minutes, not 5.** A `*/5` cron ran for months but
GitHub honoured only ~5% of it (measured: 14.9 runs/day, median gap 83 min, and
never once a 5-minute gap). See *Monitoring limits* for the measurement and
*Storage* for the checkout cost that made short intervals unaffordable.

### Secrets

| Secret | Used by | Needed for |
|---|---|---|
| `ETHERSCAN_API_KEY` | trace, scan | Arbitrum L1 tracing (free tier) |
| `BREVO_SMTP_KEY` | all alerting jobs | Email alerts (free tier) |
| `ALERT_EMAIL` | all alerting jobs | Recipient address |

Everything degrades gracefully without them — modules log and continue.

## Storage

`data/` is **~104 MB** (813 MB before compaction). Growth is bounded by:

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

`scripts/compact_data.py --apply` runs daily in `analyze.yml`. Without a scheduled
run the bound is a one-off: per-minute snapshots and re-polled `historicalOrders`
regrow `data/` to ~800 MB within weeks.

## Validation policy

`src/backtest.py` proves the scorer can pick the target out of a stranger set by
splitting his own history into two disjoint windows. Windows are chosen by
**distinct active trading days**, not by a fixed calendar period: this target
traded on 61 days across a 166-day span, so two fixed 21-day windows captured only
4 active days each while still clearing the fill floor (TWAP puts thousands of
fills in a single session). Windows widen — never relax — until both the day floor
and the fill floor are met, and the report publishes the dates, active-day counts,
fill counts, excluded days and a leakage check.

**Active days, all the way down.** The same rule governs the style metrics, not
just window selection. `episodes_per_active_day` and `fills_per_active_day`
divide by days the trader actually traded, never by the calendar span. Dividing
by the span measures how often someone *shows up*, which is regime, not identity
— and `active_days_ratio` already measures exactly that, once.

Getting this wrong is not theoretical. On 2026-08-05 the live self-match failed
with `Decision frequency 6x apart (0.17 vs 0.94 position episodes/day)`: the
target was rejected as an impostor against his own history. Both windows held the
same 12 active trading days and differed only in spread — 19 calendar days versus
72. That failure dropped the scanner into `OBSERVING` on unreachable config
thresholds with behavioural alerting off, which is the detection vector this
system exists to run. Per active day the ratio is 1.5x and the self-match passes
at 0.7461.

If you change how any dimension is measured, bump `SCORING_SCHEMA` in
`src/thresholds.py` — a ceiling proven under the old scorer is not evidence about
the new one, and `resolve()` will refuse to carry it forward.

Whatever the outcome, the scanner always operates under one of four named
policies, published in `scans/latest.json`, logged, and shown on the scanner page:

| Policy | When | Behavioural alerts | Watchlist |
|---|---|---|---|
| `CURRENT_VALIDATED` | Self-match passed | Yes, at the proven ceiling | Yes |
| `CARRIED_FORWARD` | Inconclusive, but a compatible validated ceiling exists | Yes, at the carried ceiling | Yes |
| `POPULATION_WATCHLIST_FALLBACK` | Unvalidated, calibration sufficient | Only when independently corroborated | Yes, top-percentile vs measured population |
| `OBSERVING` | Unvalidated and calibration too small | No | Evidence retained, nothing alerts |

Three rules hold in every policy:

- **Behaviour alone can never alert while the scorer is unvalidated.** Under the
  population fallback a candidate is capped at `WATCHLIST` no matter how extreme
  its percentile; only independent evidence — fund flow, HL-native transfer,
  deposit correlation, or transfer-graph linkage — promotes it.
- **A style-vetoed wallet is never promoted**, and its evidence is still retained.
- **A carried-forward ceiling is rejected when the scoring schema changes.**
  `SCORING_SCHEMA` in `src/thresholds.py` must be bumped whenever dimensions,
  weights or normalisation change; a ceiling proven under a different scorer is
  not evidence about the current one.

An inconclusive backtest exits 0 — it is neither a pass nor a daily red build.
Only an outright FAILED self-match keeps the conservative config thresholds, and a
failure never lowers them.

## Matching: thresholds and dispositions

`src/thresholds.py` is the only place match thresholds are decided. Config
declares 0.90/0.80/0.65, but those are lowered toward the self-match ceiling from
`profile/backtest.json` — the trader scores ~0.75 against their own adjacent
history, never near 1.0, so an unreachable threshold would guarantee missing the
migration. Resolved values are written into `scans/latest.json` and the dashboard
reads them, so an emailed tier and a displayed tier always agree.

"Only place" is enforceable, not aspirational: nothing may compare a similarity
score against a literal. Ask `thresholds.py` instead —

| Need | Use |
|---|---|
| "does this wallet trade like the target?" | `behavioural_gate(eff)` |
| "how strongly?" (0–1, for weighting) | `behavioural_strength(score, eff)` |
| "may this combined route alert?" | `combined_alert_ok(score, eff, vetoes, route=…)` |

`behavioural_strength` reaches 1.0 at the **proven self-match ceiling**, not at a
notional 1.0 — scaling to a score nothing can reach is what left the headline risk
number awarding a perfect re-identification under a fifth of its own weight.

Ask `utils.candidate_current_score(candidate)` for how well a wallet matches
**now**. `best_score` is a high-water mark that only ratchets up; using it to
answer a present-tense question is what let a wallet whose score had decayed to
0.13 keep emailing as a behavioural match.

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
  chain/          multi-chain collection substrate (client, spam, labels, assets)
dashboard/      SvelteKit 5 static SPA, deployed to GitHub Pages
data/           collected JSON — also the dashboard's API
profile/        computed fingerprint, recent fingerprint, backtest report
scripts/        compact_data.py, dedupe_fills_by_tid.py, backfill_transfers.py
docs/           architecture.md (see caveat below), plans, specs
```

## Transfer substrate

`src/chain/` collects transfers and writes `data/transfers/{chain}/YYYY-MM-DD.json`.
Everything downstream — the transfer graph, the tracer, linkage — reads it rather
than calling Etherscan directly.

What it does that the previous single-endpoint collection did not:

- **Six chains, not one.** Etherscan V2 serves Arbitrum, Ethereum, Base, Optimism,
  Polygon and BSC from the same API key by varying `chainid`. Configure in
  `config.json` under `chains`.
- **Three record kinds, not one.** `tokentx`, `txlist` and `txlistinternal`.
  Internal transactions matter most: a contract-mediated transfer, which is what
  every bridge emits, appears in neither of the others.
- **Block-range pagination.** The old collection asked for `page=1, offset=1000,
  sort=desc` exactly once, so `data/l1_transactions/` capped at 1000 records
  forever. The walker steps forward by block with no ceiling.
- **Poisoning quarantine.** 905 of those 1000 records moved under a dollar, and a
  single forgery of the known self-wallet accounted for 510. Forged addresses are
  matched on their first and last 4 hex characters and rolled up into
  `data/transfers_spam/latest.json` instead of entering the graph.

  The rule is **value-ordered**, not a membership test: an address is a forgery of
  another only when the other has moved strictly more value with the wallet.
  Nobody forges an address poorer than their own. A symmetric test gets this wrong
  in both directions — it lets a $1 clone of a large counterparty quarantine the
  genuine address, and a blanket exemption to prevent that whitewashes the clone.
- **Entity labels.** Phase 1 ships two tiers: the curated registry
  (`data/labels/entities.json`, naming exchanges, bridges and routers) and
  **bytecode** — every address the graph could grade is checked once with
  `eth_getCode` and cached in `data/labels/code_cache.json`, so a contract can
  never be graded a personal wallet. A lookup that fails marks nothing; it is
  retried rather than remembered as "not a contract".

  A third tier, **inferred CEX deposit addresses**, is specified and implemented
  as a pure function but is deliberately **not wired in Phase 1** — it lands in
  Phase 2 with the cross-gap re-linking that consumes it. Nothing today acts on
  an inferred label.

  **"Service" is purpose-relative.** The graph asks "may I walk into this
  address?" and a CEX *deposit* address answers no. Linkage asks "does shared use
  imply common ownership?" and the same address is the strongest possible yes —
  it belongs to one exchange account. `service_addresses()` therefore takes the
  categories to apply; linkage passes `SERVICE_CATEGORIES` minus the two deposit
  categories.
- **Prices that fail are not zero, and not spam.** A known asset the system could
  not price this run is stored with `value_basis: "price_unavailable"` and
  counted in `data/transfers/latest.json`'s `unpriced` field. It is retained, not
  quarantined: booking it as `0.0` would drop a potentially large transfer below
  every threshold silently, and quarantining it would keep it out of the
  substrate entirely. Only a genuinely unknown token is quarantined.
- **Alerts name the asset and the chain.** Collection used to be USDC-on-Arbitrum
  by construction, so both were safe to hardcode. Neither is now, and the amount
  is rendered as a dollar figure (`$X of ETH on base`) rather than a bare number
  beside a symbol, which would read as a token quantity.

**Blindness is reported, never inferred.** `data/transfers/latest.json` carries
`degraded_sources`; a chain that could not be read is recorded there, and an empty
sweep never serialises the same way as a failed one.

To investigate one address on demand, run the **Trace Fund Flows** workflow with
the `investigate_wallet` input, or locally:

```powershell
python scripts/backfill_transfers.py --wallet 0xa95d9c1f655341597c94393fddc30cf3c08e4fce
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
  Local `Ezekiel-backup-*/` trees are gitignored for the same reason — keep
  backups outside the working tree if you can.
- The dashboard has **no test harness**. Its data layer (`src/lib/api.js`) is
  verified by running it against live `raw.githubusercontent.com` data rather
  than by unit tests, so a change there needs a real fetch to prove it.
- A fingerprint written before `SCORING_SCHEMA` `2026-08-05.1` carries
  `episodes_per_day` / `fills_per_day`, which were normalised per calendar day.
  Those keys are deliberately not read any more: comparing them against the
  per-active-day ones would silently mix units. The activity dimension drops out
  for one `analyze.yml` cycle after the change, then returns.

## Monitoring limits (read this before trusting the heartbeat)

GitHub does not honour high-frequency cron on this repo. Measured over the 100
most recent `collect` runs (160.9 h span, 2026-07-27):

| | |
|---|---|
| Requested (`*/5`) | 288 runs/day |
| **Observed** | **14.9 runs/day (5.2%)** |
| Gap median / p90 / max | 83 / 160 / 220 min |
| Smallest gap ever seen | 46 min — the 5-minute schedule was never once honoured |

Two consequences:

1. **Cadence is a request, not a guarantee.** `*/15` will not give 15-minute
   resolution; expect roughly hourly in practice. Treat collection resolution as
   ~1–3 h when reasoning about detection latency.
2. **`STALE_AFTER_MINUTES` is 360**, set above the observed 220-minute maximum. A
   tighter threshold produces routine false alarms rather than useful signal.

### What the heartbeat cannot do

It is triggered by the same scheduler it supervises. If scheduling stops
completely, the heartbeat stops too and **no alert is sent**. Mitigations, in
order of independence:

1. `workflow_run` — the heartbeat also fires when any data workflow completes, so
   freshness is checked on an event rather than only on a timer.
2. `collector.check_own_freshness()` — a running collector reports a preceding gap
   inline.
3. GitHub emails the repo owner before auto-disabling schedules after 60 days of
   repository inactivity. Automated commits count as activity, so this only
   triggers once things are already fully stopped.

**Residual gap:** a total Actions outage is undetectable from inside Actions. The
only genuine external signal needs something outside this repo — a free uptime
monitor (e.g. UptimeRobot/cron-job.org) polling
`https://raw.githubusercontent.com/<owner>/<repo>/main/data/index.json` and
alerting when `last_updated` ages past ~6 h. That is a deliberate non-goal here
(no external services), so the gap is documented rather than closed.
