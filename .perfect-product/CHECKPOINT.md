# Perfect Product Checkpoint

Updated: 2026-08-05 (end of improvement pass)

## Git

- Branch: `main`
- HEAD: `a24d5ecf0`
- Baseline this pass started from: `23b28d852`
- Protected branches: `main`
- Forbidden operations: merge, tag, deploy, publish, force-push, delete-branch,
  rewrite-history. **Nothing was pushed** — all four commits are local.
- Working tree: clean.
- Existing work preserved: three untracked `Ezekiel-backup-*` directories were
  never touched; they are now gitignored so they cannot be committed by accident
  to what must remain a public repo.

## Product intent

- Thesis: track one Hyperliquid trader and answer continuously — has he moved to
  a new wallet, and which one?
- Release boundary: V1 frozen 2026-08-05, see PRODUCT-INTENT.md.
- Critical journeys: J1 detect migration · J2 operator reads an alert · J3
  operator reads dashboard posture · J4 collection keeps running · J5 data stays
  bounded and unrecoverable data is never destroyed.
- Load-bearing assumption: the scorer cannot reach high similarity even on the
  true trader. Measured self-match ceiling is now **0.7461**. Every threshold
  decision derives from that.

## Current position

- Gate: 11 (independent release audit).
- Highest-risk open item: `release.independent-audit`.
- Why: every finding in this pass was produced and verified by the implementing
  agent. That is precisely the single-judge situation this gate exists to
  prevent. The first two reviewer agents (security, adversarial) terminated on an
  API session limit; a release auditor was dispatched against `dbb8f350e`, which
  is one commit behind final HEAD.
- Exact next action: obtain a clean release-audit result against `a24d5ecf0`,
  resolve anything it raises, then re-run final evidence and
  `pp.mjs validate --final`.

## Completed and proven

| Acceptance ID | Result | Evidence | Commit |
|---|---|---|---|
| intent.product-thesis | PASS | PRODUCT-INTENT.md | dd6391485 |
| correctness.veto-invariant | PASS | test_combined_alert_refuses_a_style_vetoed_candidate | dd6391485 |
| correctness.threshold-single-source | PASS | thresholds.py helpers + tests | dd6391485 |
| correctness.risk-score-calibration | PASS | 2 unit tests + live recompute | dd6391485 |
| correctness.alert-freshness | PASS | test_..._score_has_decayed | dd6391485 |
| correctness.backtest-validation | PASS | gate1-fingerprint-backtest, final-backtest | 7319d60dc |
| data.compaction-safety | PASS | gate1-compaction-dryrun | — |
| architecture.no-duplicate-policy | PASS | _combined_route tests | dd6391485 |
| ux.dashboard-journeys | PASS (narrowed) | gate3-runtime-dashboard-data | 7319d60dc |
| content.alert-truthfulness | PASS | gate3-stale-banner | a24d5ecf0 |
| visual.dashboard-quality | PASS | final-build (no warnings) | 7319d60dc |
| security.secrets-and-privacy | PASS | credential scan, workflow audit | dd6391485 |
| resilience.malformed-state | PASS | NaN + wrong-shape tests | dbb8f350e |
| performance.budgets | PASS | build + suite timings | — |
| compat.targets | PASS | local target + test.yml parity | — |
| docs.accuracy | PASS | README | dbb8f350e |

## Current verification baseline

| Check | Result | Commit | Evidence label | Notes |
|---|---|---|---|---|
| pytest | 369 passed | a24d5ecf0 | final-integration | was 340 at baseline |
| ruff | clean | a24d5ecf0 | final-static | |
| dashboard build | exit 0, no warnings | a24d5ecf0 | final-build | unused-CSS warning fixed |
| backtest | PASS 0.7461, rank 1/21 | dbb8f350e | final-backtest | on local data through 2026-07-29 |
| live data layer | 7/7 endpoints, banner correct | a24d5ecf0 | final-runtime, gate3-stale-banner | real raw.githubusercontent.com |
| heartbeat | exits 1 locally | — | baseline-heartbeat | expected: stale local checkout, not a production stall |

## Open findings

| ID | Severity | Gate | Finding | Retest criterion |
|---|---|---|---|---|
| — | RELEASE_BLOCKER | release | `release.independent-audit` unsatisfied: no independent reviewer has confirmed this work | A release auditor runs against final HEAD and reports no blocker |
| — | MINOR | visual | `visual.dashboard-render` BLOCKED: no browser automation available | Load each route in a browser at desktop and mobile viewports |
| — | MINOR | a11y | `a11y.dashboard` BLOCKED: same reason; no accessibility work was done or claimed | Keyboard pass + contrast/semantics check in a browser |

F-001 … F-012 are all FIXED; see FINDINGS.jsonl.

## Carried, not fixed (outside boundary or judged unreachable)

- `docs/architecture.md` still describes unbuilt components; it already carries a
  staleness banner naming them, and rewriting it is outside the V1 boundary.
- `persist_candidate` builds a filename from an API-supplied wallet string.
  Judged effectively unreachable — a malformed address returns no fills and is
  never persisted — so left alone rather than hardened speculatively.
- The dashboard has no JS test harness. Its data layer is verified by running it
  against live data instead. Adding one would be new scope.
- **Production has not been re-validated.** The live self-match failure (F-010)
  is fixed in code here, but nothing was pushed, so `origin/main` is still in
  `OBSERVING` with behavioural alerting off until these commits reach it and
  `analyze.yml` runs.

## Runtime/process state

- Repository-owned servers: a `vite preview` was started on port **4173**
  (background id `bdovxqth0`) and is still listening. Stop it before finishing.
- Repository-owned browsers: none (Chrome extension not connected).
- Long-running commands: none.
- Must not run concurrently: `src/backtest.py` and `src/fingerprint.py` both
  rewrite `profile/`; two at once will produce a dirty tree and confusing
  evidence.

## Continuation instruction

Read PRODUCT-INTENT.md and ACCEPTANCE.json, verify Git state, then continue with
the exact next action above. Do not repeat completed work unless later
integration provides concrete regression evidence.
