# Product Intent — Ezekiel

Inferred from README.md, PRD.md, config.json, source, tests, workflows and Git
history on 2026-08-05. Where code and documentation disagree, code and observed
runtime behaviour were treated as authoritative and the conflict is recorded.

## Product thesis

Ezekiel tracks one Hyperliquid trader (`0x45d2…4029`, codename Ezekiel) and
answers a single question continuously:

> **Has this trader moved to a new wallet, and if so, which one?**

The owner copy-trades this wallet manually. Losing the trader is the failure
condition the entire system exists to prevent.

### Why the problem is hard — this shapes every design decision

1. **Hyperliquid's API serves only ~2,000 recent records per endpoint.** History
   not captured is permanently unrecoverable. A collection gap is irreversible
   data loss, not an inconvenience.
2. **A migration is deliberately hard to see.** Funds can move inside Hyperliquid
   with no L1 footprint, or through a CEX gap that breaks on-chain custody.
3. **The scorer cannot reach high similarity even on the true trader.** Measured
   self-match ceiling is **0.7163** (`profile/backtest.json`, validated
   2026-07-29, schema `2026-07-27.1`). Any threshold above that guarantees the
   false negative the system exists to avoid. This single fact drives the whole
   threshold architecture — and is the root of the defects found in this pass.

## Target users and context

| User | Context and abilities | Primary need | Trust/safety constraints | Success feels like |
|---|---|---|---|---|
| The repository owner (sole operator) | Technically capable; reads JSON and Actions logs; consumes the product mainly as **email alerts** and a **read-only dashboard**; not on call | Know immediately and reliably if the trader moved, and to where | A false CRITICAL alert devalues every future alert; the repo is public, so nothing secret may be stored | "I can trust the inbox, and the dashboard tells me the posture in ten seconds" |

## Critical jobs and journeys

| ID | Journey | Trigger | Desired outcome | Failure conditions | Required evidence |
|---|---|---|---|---|---|
| J1 | Detect migration and name the successor wallet | Target goes quiet / funds move | Successor surfaced at an actionable tier | Missed migration | Unit + runtime on real data |
| J2 | Operator reads an alert email and decides | Alert fires | Alert is truthful, evidenced, actionable | False//stale CRITICAL alert | Source + test of every alert route |
| J3 | Operator reads dashboard posture | Opens Pages site | Risk, candidates, transfers match the data and the emails | Dashboard disagrees with alerts | Real-runtime journey + capture |
| J4 | Collection keeps running; staleness noticed | Cron / workflow_run | Stall produces an alert | Silent stall → unrecoverable loss | Runtime + source |
| J5 | Data bounded; unrecoverable data never destroyed | Daily compaction | `fills`/`funding`/`ledger`/`l1_transactions` never compacted | Irreversible loss | Source + round-trip test |

## Intended experience and identity

### Must feel

- **Recall-biased, precision-gated.** Missing the migration is far worse than a
  watchlist entry — but an alert must mean something.
- **Graded, never asserted.** "A transfer is not ownership." Every wallet carries
  a classification, a confidence and the evidence behind it.
- **Auditable.** Every alert and score carries its reasoning, so a conclusion can
  be checked rather than trusted.

### Must not become

- A generic "crypto wallet analytics dashboard". It tracks one trader for one
  purpose.
- A system that asserts identity it has not proven.
- A noisy inbox.

### Protected distinctive elements

- The four-policy validation state machine (`CURRENT_VALIDATED`,
  `CARRIED_FORWARD`, `POPULATION_WATCHLIST_FALLBACK`, `OBSERVING`).
- The three invariants in README "Validation policy".
- Three-way disposition (ALERT / WATCHLIST / BACKGROUND) — suppression never
  discards evidence.
- Free-tier only: no servers, no paid services, JSON in Git is the database.

## Supported platforms and operating conditions

- Operating systems: GitHub Actions `ubuntu-latest` (production); Windows 11 +
  PowerShell (local development).
- Browsers/runtimes: Python 3.12; Node 22; modern evergreen browsers.
- Viewports/zoom: desktop and mobile; dashboard is read-only and responsive.
- Inputs: pointer and keyboard.
- Online/offline: dashboard requires network (fetches JSON from
  `raw.githubusercontent.com`); no offline mode promised.
- Data scale: `data/` ~104 MB; scans ~500 leaderboard wallets; transfer graph
  budgeted (`max_nodes` 300, `time_budget_seconds` 150).

## Release boundary

### In scope (V1 — frozen 2026-08-05)

Correctness and internal consistency of the three detection vectors and the
decisions they drive; truthfulness of alert content; accuracy of the dashboard
against the data it renders; data-integrity safety of compaction; documentation
that matches the code.

### Non-goals

New detection vectors; external uptime monitoring (explicit non-goal — no
external services); `profile_builder.py` productisation; re-introducing Twitter
collection.

### Compatibility and migration promises

- `scans/latest.json` keeps the legacy `similarity_*` threshold shape readable
  (`thresholds.normalise`); the dashboard has the matching fallback.
- `SCORING_SCHEMA` must be bumped whenever dimensions, weights or normalisation
  change, so a carried-forward ceiling is rejected rather than silently reused.

### Acceptable known limitations

- GitHub honours ~5% of the requested cron; detection resolution is ~1–3 h.
- A total Actions outage is undetectable from inside Actions (documented
  residual gap; closing it requires an external service, a stated non-goal).
- The repo must stay public, so `research/` is world-readable.

## Success and failure conditions

**Success:** a migration produces a correctly-named successor wallet at a tier
the operator acts on; no CRITICAL alert is factually false; the dashboard agrees
with the data and with the emails; no unrecoverable data is destroyed.

**Failure:** a vetoed or stale wallet reaches the inbox as CRITICAL; the headline
risk score misrepresents the posture; the operator stops trusting alerts;
collected history is lost.

## Constraints and protected state

- Current branch: `main` (preserve).
- Protected branches: `main`.
- Forbidden operations: merge, tag, deploy, publish, force-push, history rewrite,
  branch deletion.
- Existing in-progress work: three untracked `Ezekiel-backup-*` directories —
  **not mine to touch**; must remain untracked and unmodified.
- Privacy: repository is public; no secrets in code; alert code must name missing
  env vars, never their values.
- Required interfaces: `data/**/latest.json` shapes consumed by the dashboard.

## Assumptions and contradictions

| ID | Assumption or conflict | Evidence | Confidence | Consequence if wrong | Validation |
|---|---|---|---|---|---|
| A1 | Local HEAD code == `origin/main` code | `git log HEAD..origin/main` = 261 commits, **all** `[automated]`; `git diff HEAD origin/main -- src/ tests/ dashboard/src/ scripts/ .github/ config.json` empty | High | Work applied to stale code | Re-checked before final report |
| A2 | Local heartbeat STALE is a checkout artefact, not a production stall | `origin/main` carries automated data commits through 2026-08-05 | High | Would mask a real outage | Verified via `git fetch` |
| A3 | Self-match ceiling 0.7163 is current and schema-compatible | `profile/backtest.json` schema `2026-07-27.1` == `SCORING_SCHEMA`; re-ran `src/backtest.py` 2026-08-05 → PASS | High | Thresholds mis-adapted | Re-run in final validation |
| A4 | Operator wants recall bias preserved | Stated repeatedly in README and code comments | High | Fixes could over-suppress | Every fix checked for recall impact |
| C1 | README: *"a style-vetoed wallet cannot be promoted on any route"* — **`src/tracer.py` violates this** | `tracer.py:348-359` calls `alert_combined_match` with no `can_alert()` check | Confirmed | False CRITICAL alerts | F-001 |
| C2 | README: `thresholds.py` *"is the only place match thresholds are decided"* — **four modules bypass it** | Only `scanner.py`/`backtest.py` call `th.resolve()`; `risk.py`, `tracer.py`, `transfer_graph.py` use a hardcoded `0.65` | Confirmed | Incoherent decisions | F-002, F-003 |
| C3 | README "expect: 156 passed" | Actual suite: **340** passed | Confirmed | Misleading setup docs | F-006 |
| C4 | `docs/architecture.md` describes components that were never built | Self-flagged in README "Known rough edges" | Confirmed | Misleads future work | F-007 |
