# Perfect Product Checkpoint

Updated: 2026-08-05, after the second independent release audit.

## Git

- Branch: `main`
- HEAD at certification: **`33208e7c1`** (this file's own commit follows it)
- Baseline this pass started from: `23b28d852`
- Protected branches: `main`
- Forbidden operations: merge, tag, deploy, publish, force-push, delete-branch,
  rewrite-history. **Nothing was pushed** — every commit is local and
  `origin/main` is untouched.
- Working tree: clean.
- Existing work preserved: three untracked `Ezekiel-backup-*` directories were
  never touched; now gitignored so they cannot reach a public repo by accident.

## Product intent

- Thesis: track one Hyperliquid trader and answer continuously — has he moved to
  a new wallet, and which one?
- Release boundary: V1 frozen 2026-08-05, see PRODUCT-INTENT.md.
- Critical journeys: J1 detect migration · J2 operator reads an alert · J3
  operator reads dashboard posture · J4 collection keeps running · J5 data stays
  bounded and unrecoverable data is never destroyed.
- Load-bearing assumption: the scorer cannot reach high similarity even on the
  true trader. Measured self-match ceiling is **0.7461**; every threshold
  decision derives from it.

## Current position

- Gate: 11 complete. Two independent audits ran; the second, against the frozen
  commit, found **no release blocker in code or behaviour**.
- Acceptance contract: **20 of 20 items PASS.**
- Exact next action: **push, then watch `analyze.yml`.** Production is still
  running the old code — `origin/main` is in `OBSERVING` with behavioural
  alerting off because of F-010. Nothing in this pass reaches the operator until
  these commits land and the daily job re-runs the self-match on data through the
  present. Confirm afterwards that `data/scans/latest.json` reports
  `CURRENT_VALIDATED` and that `profile/backtest.json` shows `passed: true` under
  scoring schema `2026-08-05.1`.

## Completed and proven

All 19 PASS items and their evidence are listed in ACCEPTANCE.json; the
verification table is in FINAL-REPORT.md §4. Headlines:

| Area | Result |
|---|---|
| Suite | 375 passed (340 at baseline) |
| Lint | clean |
| Production build | exit 0, no warnings |
| Self-match | PASS 0.7461, rank 1/21, margin 0.1434, no self-veto |
| Visual | 6 routes × 2 viewports rendered in real Chrome, no page errors |
| Mutation test | 8/8 re-introduced defects caught |
| Independent audit | CONDITIONAL — no release blocker in code or behaviour |

## Current verification baseline

Certified as one atomic pass at **`33208e7c1`**, tree confirmed clean
immediately before and after each run: `static`, `integration`, `build`,
`runtime`. `backtest` and the visual renders carry forward from `62713a25c`;
`git diff 62713a25c 33208e7c1 -- src/ tests/ dashboard/src/ scripts/ config.json`
is empty, which the release auditor verified independently.

## Open findings

| ID | Severity | Gate | Finding | Retest criterion |
|---|---|---|---|---|
| — | MINOR | compat | RM-18: ubuntu CI parity inferred from `test.yml` running identical commands, not executed here (no ubuntu runner available). | Run the workflow. |

Accessibility was closed by fixing three real defects, not by waiving the gate:
`--text-muted` at 2.13:1 on a hovered card, nine chart canvases with no text
alternative, and no explicit focus ring. Final review 0 MAJOR / 0 MINOR. The
review is agent-executed and covers semantics, focus order, labelling,
colour-independence and contrast maths — **not** a screen-reader session and
**not** a certification.

F-001 … F-014: **13 FIXED, 1 CARRIED** (F-007, `docs/architecture.md` — it already
carries an accurate staleness banner and rewriting it is outside the V1
boundary). See FINDINGS.jsonl, whose `status` fields now agree with this
sentence; a third independent audit caught them disagreeing.

## Carried, not fixed (outside boundary or judged unreachable)

- `docs/architecture.md` still describes unbuilt components; it already carries a
  staleness banner naming them, and rewriting it is outside the V1 boundary.
- `fingerprint.load_account_latest()` is dead code — defined, never called.
  Confirmed by the release auditor. Harmless; left rather than removed, since
  deleting it is not required by any acceptance item.
- `persist_candidate` builds a filename from an API-supplied wallet string.
  Judged effectively unreachable — a malformed address returns no fills and is
  never persisted — so left rather than hardened speculatively.
- The dashboard has no JS test harness; its data layer is verified by running it
  against live data instead.
- Stranger ordering in `backtest.json` is non-deterministic among ties, creating
  churn in a Git-backed store.

## Runtime/process state

- Repository-owned servers: **none running.** The `vite preview` instances on
  ports 4173 and 4174 were both stopped; verified nothing is listening.
- Repository-owned browsers: none. Headless Chrome was invoked per-render with an
  isolated `--user-data-dir` and exited each time.
- Long-running commands: none.
- Must not run concurrently: `src/backtest.py` and `src/fingerprint.py` both
  rewrite `profile/`. Separately, **never run the mutation harness while an audit
  is in progress** — doing so is what caused the first audit's BLOCK verdict.

## Continuation instruction

Read PRODUCT-INTENT.md and ACCEPTANCE.json, verify Git state, then continue with
the exact next action above. Do not repeat completed work unless later
integration provides concrete regression evidence.
