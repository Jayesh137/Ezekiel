# Wallet discovery implementation and verification

The approved discovery overhaul is implemented on `improve/wallet-discovery`.
The original uncommitted `docs/2026-09-25-wallet-discovery-review.md` is preserved.
The user subsequently authorized release. The overhaul and independent-review
corrections were released in [PR #34](https://github.com/Jayesh137/Ezekiel/pull/34).
Live verification then exposed the throttling/resumption issues addressed below.
There are no OpenAI API calls, AI inference services or new mandatory paid keys.

## What changed

| Failure in the previous approach | Implemented change |
|---|---|
| One economic movement looked like multiple exits | Reconcile ledger withdrawals, payouts, owned transfers and decoded routes before matching. Use one-to-one assignment and expose alternatives. |
| Live and historical fingerprints used different evidence | Normalise terminal order states, align available features, apply dated historical inputs and invalidate incompatible validation schemas. |
| Sparse wallets and previous leads disappeared | Keep the full candidate registry, preserve positive facts through read failures, cache fills with coverage and revisit old recipients fairly. |
| Discovery mostly saw large leaderboard accounts | Seed from both participants in public trades and small deposits, rotate markets, reserve investigation capacity and support optional streaming/import. |
| Bridges and shared routers obscured destinations | Join exact source decodes and verified protocol identifiers, retain unresolved routes and source roles, and use keyless Base/Optimism readers. |
| Current snapshots lost previous authority relationships | Retain dated authority actions and successful snapshots; model approval, expiry, revocation and observed intervals. |
| Simple timing/style matches produced weak leads | Reconstruct trading episodes, compare independent sessions/regimes, test one-to-one timing against shifted sessions, and rank funding/return/handoff hypotheses. |
| Several detectors repeated one fact | Group evidence sharing parents, distinguish categories from independent groups and reserve confirmed status for configured trusted seeds. |
| Validation could use selected or future information | Freeze a score-blind control cohort prospectively, match feature availability and replay observation-to-ranking chronologically. |
| Temporary runners discarded useful history | Make scan the sole checkpoint owner. Import idempotent watch/trace observation shards, preserve cohort/scheduling metadata and acknowledge artifacts before cleanup. |
| A list of scores did not explain what to do | Add Investigations with next checks, factual-route and sparse-session filters, counterevidence, references, unresolved routes and separate attempt/success timestamps. |

The integration review also fixed a route interface mismatch (`hl_account` versus
`recipient`), connected observed inbound transfers to return-route investigations,
normalised shared fill references and numeric timestamps, retained authority beyond
the short market window, isolated corrupt observation shards, and excluded ignored
SQLite/replay artifacts from the Git size guard. Tests reproduce these failures.
The final route review also requires matching burn-token identity, raw amount and
sender relationship, with an unambiguous transfer leg, before binding a source
message. Equal dollar values or transaction-level recipients cannot resolve an
otherwise ambiguous multi-transfer transaction.
Investigation scheduling reserves capacity for wallets without funding/authority
links, so a busy factual queue cannot indefinitely starve public-trade discoveries.

New funding and return observations describe immediate counterparties or decoded
protocol instructions. They do not reveal an exchange's underlying customer.
Unsolicited small transfers, common signals, shared execution services and incomplete
history remain alternative explanations; research scores do not establish ownership.

## Runtime and operator use

Read [discovery operations](discovery-operations.md) for exact commands and recovery.
The default works with public polling. Optional local streaming needs an existing
machine and the separate streaming dependency; no always-on host was provisioned.

Scan restores/imports, polls, indexes routes, enriches, ranks, publishes quality and
checkpoints. Watch retains its independent concurrency group and hands over facts
without overwriting scanner state. Daily analysis restores the scanner's dated
control cohort. Reports remain bounded JSON; raw observations stay in ignored SQLite
and branch-scoped artifacts. Checkpoint preparation is distinct from upload success.
Missing/corrupt/expired artifacts and pending batches are reported explicitly.

The dashboard reads committed reports on `main`; branch previews show “not collected”
until reports exist there. Both synthetic local fixtures and the deployed dashboard
have been verified in isolated desktop and phone browser sessions.
Infrastructure classification and configured controlled recipients have separate
accounting labels. Existing mobile review links to Investigations and uses honest
lead labels, including for legacy inferred confirmations.

## Verification

Verification ran on 26–27 September 2026, with the final full regression after
the independent-review fixes and protocol-join regression:

- Full Python suite: **1,851 passed in 141.90 seconds**. Baseline: 1,696 passed.
- Ruff: **all checks passed**.
- Dashboard unit tests: **51 passed**. Baseline: 47 passed.
- Production dashboard build: **passed**; existing Svelte accessibility warnings
  and the existing static-adapter fallback warning remain. The Windows sandbox
  blocked the bundler initially; the authorized build outside it passed.
- PWA build check: **passed** (manifest, five required files, ten pages with tags).
- Apps Script relay tests: **13 passed**.
- Workflow independence/runtime regression checks: **passed**; all four modified
  workflow YAML files parsed successfully. The release CI, Pages deployment and
  live watch run **36314797322** passed. Live scan and trace observations uploaded
  successfully even when enrichment steps hit their time limits.
- Isolated headless Chrome: desktop 1440px and phone 390px rendering, search,
  evidence expansion, missing-report state, no horizontal overflow and no runtime
  exceptions **passed**. Screenshots were visually inspected. The in-app browser
  bridge timed out twice; a separate local, temporary-profile browser was used.
- Repository size guard: **no file within 20 MiB of the GitHub limit** after correctly
  excluding local artifacts. `git diff --check` passed; production data has no diff.

Earlier task-7 full-suite failures concerned changed evidence-family expectations
and test path isolation; they were addressed and the final full suite above passed.
Red/green regressions cover the new failure cases. Detailed logs and screenshots
remain in the ignored `.superpowers/sdd/2026-09-26-wallet-discovery/` workspace.

Read-only live probes during implementation observed the public Hyperliquid trade
schema (20 events / 22 wallets in an isolated collector run) and successful responses
from six Base/Optimism transfer endpoints. They changed no historical production
observations and sent no notifications. A subsequent read-only Circle Iris probe
returned a complete source message for transaction
`0xff8c1906227c7ddd80cf00b4541a75aaa6c96b8e560c43502243759b71980e0a` on Arbitrum.
The decoder recovered the USDC token, $3.5M burn, extension sender, and target's
Hyperliquid recipient, matching the stored transfer. This verifies one real route,
not destination credit or coverage of every protocol version.

## Live runtime follow-up

Production scan **36341703379** exhausted its 22-minute step while repeating
rate-limited fill requests. Trace **36338504480** exhausted its three-minute
dormancy step. Upload and import succeeded, but these steps did not finish their
reports. The follow-up addresses the measured causes:

- Pace Hyperliquid reads by request/response weight at 600 weight/minute, within
  the documented 1,200 weight/minute public IP allowance. A 2,000-fill response
  costs more than a small state read. Stop the batch on HTTP 429 and publish
  partial coverage, instead of retrying each remaining wallet.
- Bound scanner reads to 15 minutes, reserve time for reports/control observations,
  and retain oldest-attempt ordering in the checkpoint. Priority batches reserve
  exploration slots; incomplete priority reads are not immediately repeated in
  the leaderboard phase. Controls cannot monopolize every run.
- Persist the last fully processed fill-page boundary atomically with its rows.
  Next runs overlap that boundary, retain timestamp-saturation gaps, restart when
  a wider lookback is needed, and invalidate coverage if retained fills are pruned.
  Disjoint historical windows cannot masquerade as continuous history.
- Check dormancy with portfolio birth first; only plausible handoff windows need
  a recent-trades request. Use a two-minute read budget, persistent fair rotation,
  and explicit failed/deferred coverage. Preserve unread prior findings as stale;
  they cannot re-alert or appear as fresh roster evidence.
- Show partial enrichment and deferred counts in Scanner and Investigations.

The production checkpoint from scan **36341703379** was downloaded and restored
into an isolated database using the production restore routine. Integrity passed:
200 market events, 148 discovered accounts, 3,734 routing/authority observations,
252,246 cached fills, and five acknowledged observation imports. Recompaction
enforced the 250,000-fill cap. No control cohort had accumulated at that point;
behavioural promotion remains unvalidated.

Follow-up verification on final committed code: **1,869 Python tests passed in
207.36 seconds** through the required pre-push hook. Dashboard **52 tests**,
production build/PWA, Ruff and isolated desktop/phone partial-scan rendering passed.

Rate accounting source: [Hyperliquid rate limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits).

## Limits and review status

An independent whole-branch review subsequently completed and found four issues:
transaction-wide route attribution, non-resumable Blockscout backfills, observer
cursors advancing despite failed artifact uploads, and control profiles missing
order data. All four have regression coverage and implementation corrections.
The review also prompted fair source-message retries, daily control-history
retention, and an authority snapshot retry outbox. Helper agents reached their
usage limit before implementing fixes; the primary session implemented and
verified them. The final patches have manual integration review rather than a
second independent agent review.

No newly attributed wallet was verified in this implementation exercise, and no
real-world recall/precision improvement is claimed. Synthetic replay tests the
pipeline; identification performance needs dated, independently verified cases.
Prospective control history must accumulate before behavioural promotion can become
validated. Polling and finite upstream retention leave gaps; CEX/private routes can
remain unresolved. Storage caps and artifact expiration also limit recoverable
history. These limits are represented in the data and operator views.

Browser fallback references: [Chrome headless](https://developer.chrome.com/docs/automation-and-testing/headless)
and [DevTools request interception](https://chromedevtools.github.io/devtools-protocol/tot/Fetch/).
