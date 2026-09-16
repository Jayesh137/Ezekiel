# HL Account Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ask every detector candidate for its sub-accounts, referral and vault deposits, and turn what Hyperliquid declares into roster evidence and alerts.

**Architecture:** A pure module (`src/hl_surface.py`) parses and derives links; a script (`scripts/check_hl_surface.py`) does paced strict reads and writes `data/hl_surface/latest.json`; `src/roster.py` reads that file as its own source (explicit links, a `referral` vector, operator groups).

**Tech Stack:** Python 3.12, pytest, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-16-hl-surface-design.md`

## Global Constraints

- Tests network-free; never write real `data/` (monkeypatch `DATA_DIR`/dirs to `tmp_path`).
- Reads through `src.cctp_feed.strict_post`, never `utils.hl_post`.
- Failed read → `None` in readings and counted in `unreadable`; never `[]`.
- Cluster = `target_wallet` + `known_self_wallets` from config only.
- Quiet referral code: `QUIET_REFERRALS = 10` accounts; unmeasured never counts.
- Sub-account roster row floor: `MIN_SUB_VALUE_USD = 1000.0`.
- Alert severities: CRITICAL / HIGH only.
- `python -m ruff check src/ tests/ scripts/` and `python -m pytest -q` green before each commit.

---

### Task 1: Pure module `src/hl_surface.py`

**Files:** Create `src/hl_surface.py`, `tests/test_hl_surface.py`

**Interfaces — Produces:**
- `parse_subaccounts(payload, master: str) -> list[dict] | None` rows `{address, master, name, account_value: float|None}`
- `parse_vaults(payload) -> list[str] | None`
- `referral_reading(payload) -> dict | None` → `{referred_by: str|None, code: str|None, referred: list[str]}`
- `referrers_to_measure(readings: dict, sizes: dict, now: datetime, limit: int) -> list[str]`
- `build_report(readings: dict, referrer_sizes: dict, cluster: set, shared_vaults: set) -> dict` with keys `subaccounts, referrals, vault_deposits, links`
- `new_links(previous: dict | None, current: dict) -> list[dict]`; link identity = `(kind, address, linked_to)`
- `HL_SURFACE_DIR`, `QUIET_REFERRALS`, `MIN_SUB_VALUE_USD`, `REFERRER_RECHECK_DAYS = 7`, `save(report)`

Link kinds: `subaccount` (address=sub, linked_to=master; emitted when master or sub in cluster), `referral` (address=referred, linked_to=referrer; emitted when either side in cluster AND `referrer_accounts` measured ≤ QUIET), `referral_pair` (neither in cluster, both sides among readings), each with `why`.

- [ ] Tests: null→[]; list→rows (lower-cased, accountValue from `clearinghouseState.marginSummary`); `{}`/str→None. Vaults list→addresses; `{}`→None. Referral populated→fields; `{}`→None. Sub-account of target → link; candidate that has a cluster wallet as sub → link; quiet referral from cluster → link; public (11) and unmeasured → no `referral` link; non-cluster pair → `referral_pair`; shared vault removed; `new_links(None, r)` returns all; unchanged returns []; `referrers_to_measure` skips fresh cache and respects limit.
- [ ] Run, fail; implement; run, pass; ruff; commit `feat(hl_surface): parse sub-accounts, referrals and vaults for any wallet`.

### Task 2: Script `scripts/check_hl_surface.py` + alerts + workflow

**Files:** Create `scripts/check_hl_surface.py`, `tests/test_check_hl_surface.py`; Modify `src/alerts.py` (add `alert_cluster_subaccount`, `alert_cluster_referral`), `.github/workflows/trace.yml`.

**Interfaces — Consumes:** Task 1. `roster.detector_candidates`, `cctp_feed.strict_post`.
**Produces:** `wallets_to_check(config, roster) -> list[str]`; `run(config, roster, post, previous, *, now, budget_seconds, sleep, clock) -> dict`; `main() -> int`.

- [ ] Tests: a raising post for one endpoint → that field None, `unreadable` counted, other fields kept; budget exhausted → remaining wallets in `not_reached` and absent from readings; referrer sizes cached from previous when fresh; alert functions called once for a new cluster link and not for an existing one (monkeypatched); alert subjects start with `[EZEKIEL] CRITICAL:` / `[EZEKIEL] HIGH:`.
- [ ] Workflow step after "Check agents" with the independence condition and `timeout-minutes: 6`; existing `tests/test_workflow_step_independence.py` must pass.
- [ ] Commit `feat(hl_surface): read every candidate's sub-accounts, referral and vaults`.

### Task 3: Roster integration

**Files:** Modify `src/roster.py`; Create `tests/test_roster_hl_surface.py`.

- [ ] `VECTOR_REFERRAL = "referral"` (one vote). Read `data/hl_surface/latest.json`: `subaccount` links → `VECTOR_EXPLICIT` on both non-target sides; `referral` links → `VECTOR_REFERRAL`; `referral_pair` → evidence on both; vault deposits → evidence; sub-account rows added when value ≥ floor, or master in cluster, or linked.
- [ ] Operator groups applied after all vectors, before tiering: union vectors over master+subs (non-service members only), record `operator_group`, `vectors_via_group`, tier on union, confidence max over group. Second pass: sub-accounts of CONFIRMED/PROBABLE masters get rows regardless of value.
- [ ] Tests: sub of target → CONFIRMED; master with correlation + sub with dormancy → both PROBABLE with `vectors_via_group`; service master lends nothing; $0 sub of POSSIBLE master absent; $0 sub of PROBABLE master present; quiet cluster referral → POSSIBLE with `referral`; roster still builds with the file missing.
- [ ] Commit `feat(roster): sub-accounts share their master's evidence; referral is a vector`.

### Task 4: Scanner referral/vault fix

**Files:** Modify `src/scanner.py:1246-1325`; Create `tests/test_scanner_referral.py`.

- [ ] `_load_target_referral_addresses` uses `referral.referred_by` + `referral.referred`; `_check_referral_link` same over the candidate payload, prints on failure; `_check_vault_overlap` excludes `hl_shared_destinations`.
- [ ] Tests against the real payload shape (`referredBy: {referrer, code}`, `referrerState.data.referralStates[].user`).
- [ ] Commit `fix(scanner): referral check read keys the API never returns`.

### Task 5: Contract-grading bug (systematic debugging)

- [ ] Reproduce: why `0xa0b86991…` (Ethereum USDC contract), WETH and vitalik.eth grade POSSIBLE with "Actively trading on Hyperliquid after receiving funds". Root-cause before fixing; test; commit.

### Task 6: Cleanups and record

- [ ] `config.json` watch `why` for `0xdd53c529…` states its current basis honestly.
- [ ] CLAUDE.md: new vector row, liquidation-distance measured-and-rejected entry, contract-grading finding.
- [ ] Full suite + ruff; push; dispatch `analyze.yml` and `trace.yml`; verify the new step's output in the run log.
