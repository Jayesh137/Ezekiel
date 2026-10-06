# Wallet discovery operations

These collectors use public Hyperliquid/blockchain observations. No OpenAI API, AI model calls, paid data plan or wallet credentials are required. Publicly observed accounts are investigation leads; activity or similarity does not establish who controls them.

## Public market discovery

Run from the repository root with the existing Python environment:

```powershell
.\.venv\Scripts\python.exe scripts/collect_market_discovery.py --once
```

The bounded default polls preferred target markets plus rotating exploration markets, including HIP-3. Each public execution identifies buyer and seller. This includes accounts outside the leaderboard and imposes no minimum account balance. The scanner reserves up to 20 investigations per run for these accounts (`discovery.scan_budget`, maximum 50). Wallets already investigated yield their allocation to others. This source cannot independently trigger an ownership alert.

Polling observes short snapshots. It does **not** cover the time between runs. The report retains event time ranges, failures, invalid records and offline/reconnect observations. Upstream private fill history exposes only recent available data; pagination/cache coverage explicitly reports saturation, page limits and errors.

Optional live collection on an existing machine:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-stream.txt
.\.venv\Scripts\python.exe scripts/collect_market_discovery.py --stream --seconds 3600 --markets BTC ETH xyz:NVDA
```

The websocket collector limits runtime, connections, message size and buffers. It records reconnect gaps and retrieves an available snapshot after a disconnect; that snapshot does not prove complete recovery. Start another bounded run to rotate exploration markets. The optional package is unnecessary for polling and import.

Import local JSONL without network access:

```powershell
.\.venv\Scripts\python.exe scripts/collect_market_discovery.py --import-jsonl path/to/trades.jsonl
```

Each line can be a public trade, an array of trades, or a `{"channel":"trades","data":[...]}` envelope. Inputs must contain the actual public schema, including `coin`, `time`, `tid`, `users`, `px`, `sz` and `side`. There is no automatic archive download or requester-pays access. Import provenance and malformed-line counts remain visible. Observations predating the store's published pruning watermark are skipped to prevent replay double-counting after compaction; use a separate `--db` for historical research.

The default SQLite store is `data/.local/discovery.sqlite3` (ignored by Git), with transactional event deduplication and separate wallet aggregates. Public event bodies retain at most 30 days/500,000 events; per-account first/last observed timestamps and counts survive pruning. “First observed” is not account creation. Spot quotes are not converted to USD. Enriched candidate fill caches retain at most 50,000 observed fills per wallet, independently of upstream availability. A bounded JSON report is written to `data/discovery/latest.json`; `--db` and `--output-dir` can isolate research runs from production data.

Sources: [Hyperliquid public trade subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions), [info endpoint and retention](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint), [public request limits](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits), and [optional websocket client](https://websockets.readthedocs.io/en/stable/reference/sync/client.html). The free `recentTrades` response was also verified directly against the public endpoint on 26 September 2026.

## Funding routes and authority history

```powershell
.\.venv\Scripts\python.exe scripts/index_discovery_routes.py
.\.venv\Scripts\python.exe scripts/index_discovery_routes.py --resolve-sources
```

The first command uses stored data only. The second additionally asks Circle's free message service about up to 20 known CCTP source transactions, under a time budget. Parsed source domain, transaction identity and message nonce must agree; different burns in a batch retain their own recipients. Protocol identifiers or exact source calldata can join a route. Similar amounts and shared public routers cannot. The report distinguishes funding instructions from confirmed credit and original funders from protocol callers. Unresolved routes include the last exact observation, missing evidence and next useful query.

Base and Optimism now use the free Blockscout instance readers for ERC-20, native and internal transfers. Contract identity is retained for the existing counterfeit-token filters. All six endpoint shapes were checked live on 26 September 2026. Pagination limits/errors stay visible; a partial descending walk does not advance past unread older transfers. A very busy address may need a larger backfill budget. Other chains retain their existing reader and explicit coverage status.

All decoded Circle logs and legitimate small forwarder deposits are retained locally. Up to five of the reserved discovery scans can investigate deposits without a balance floor. The published large-deposit correlation pool remains bounded. Address poisoning and forged tokens remain excluded from financial evidence.

`--actions actions.json` imports a JSON array of dated authority observations with `event_id`, `account`, `authority`, `kind`, `ts_ms`, `success: true`, and optional `valid_until_ms`/`slot`. Supported kinds are `approve_agent`, `revoke_agent`, `subaccount`, `multisig_signer`, and `revoke_multisig_signer`. Use the actual account and authorized address from a verified source; do not infer them from transaction signers. The existing agent collector also preserves successful snapshots. Their boundaries are marked as observation times, since polling cannot establish the exact time a permission changed. Revocation, expiration and an as-of cutoff constrain overlaps. Shared agents identify a common operator, which can be a service; they do not establish common ownership.

For an isolated offline run, provide `--data-dir`, `--db`, `--output-dir`, and optionally `--routes` containing `records`, `bridge_decodes`, and `circle_events`. The bounded report is `data/routes/latest.json`, and factual discoveries join the persistent candidate registry.

Sources: [Circle message service](https://developers.circle.com/api-reference/cctp/all/get-messages-v2), [Circle message fields](https://developers.circle.com/cctp/references/technical-guide), [Circle hook parser](https://github.com/circlefin/hyperevm-circle-contracts/blob/master/src/messages/CctpForwarderHookData.sol), and [Blockscout API schema](https://github.com/blockscout/blockscout-api-v2-swagger/blob/main/swagger.yaml).
# Successor investigations

`python scripts/check_successor_hypotheses.py` builds `data/investigations/latest.json`
from stored target fills, candidate fill caches and route/authority observations.
It makes no network calls and sends no alerts. `--input fixture.json --data-dir
scratch/replay --as-of-ms 123456789` supports offline research; input contains
`target`, `candidates` and optional `context`. Each account has `wallet`, `fills`
and optional complete dated `positions_history` snapshots. `--disclosures claims.json`
accepts manual `{wallet, observed_at_ms, url, claim}` records as hypotheses.

Investigations preserve exact funding/return routes even when execution style differs.
They compare reconstructed episodes and weekly regimes, quantity handoffs, funding
before first observed trading, observed authority overlap, reactivation and style
changes. Independently linked groups of two to four wallets may be compared as a
combined book; similar style alone never authorises a group. Missing historical
equity stays unknown; capital-normalised size measures gross episode turnover,
not leverage. A gap between fills does not prove inactivity. Public claims never
become identity ground truth.

Timing uses distinct matched decisions, at least five independent sessions and
six whole-session day shifts. The legacy `same_hand` key means a leading timing
pattern only. Shared signals, news and execution software remain alternatives.
Copier-cohort research needs at least three demonstrated prior following patterns
and three observed following patterns after a dated migration. All new research
outputs have `promotable: false`; experimental timing alone cannot send an alert.

The queue reads up to 10,000 cached fills per candidate, examines at most 100
candidates by default (200 maximum), reserves 20% of capacity for discoveries without
a funding/authority link, rotates oldest investigations within both groups, and
publishes bounded references plus original counts. Unused capacity crosses groups;
a one-wallet budget takes the oldest investigation regardless of source. Scheduled wiring
and cache persistence are described with the workflow integration below.

## Evidence quality and replay

The roster reserves `CONFIRMED` for operator-configured trusted seeds. Other tiers
are investigation priorities. Shared delegates, subaccount relationships, private
transfers and style matches remain separate assertions; none alone establishes
beneficial ownership. Shared parent IDs collapse duplicate evidence. Legacy
financial reports without parent IDs conservatively count as one dependent group.
`detector_count` and `vector_count` distinguish report names from evidence groups.
Explicit empty subaccounts remain watched even when their master is only a weak lead.

Calibration now uses the candidate's actual feature mask, excludes that candidate
and the held-out cohort, and deduplicates wallets. The scanner captures score-blind
leaderboard profiles in SQLite and freezes up to 50 hashed-address cohort members
once at least 20 independent controls are available. Membership does not change
with scores. Historical validation only uses cohort profiles observed before its
cutoff and a cohort frozen before the trial window, with at least five independent
sessions per control. A new installation will be inconclusive until enough forward
history exists; it cannot retroactively manufacture a validated trial. Profiles
retain at most 90 days / 10,000 rows. The scorer/policy schema is `2026-09-26.2`.

Run an offline chronological replay with explicitly supplied observations:

```sh
python scripts/evaluate_discovery.py --events replay-events.json --scenarios replay-scenarios.json --data-dir scratch/replay --cutoff-ms 1790000000000
python scripts/evaluate_discovery.py --quality-only
```

Events are JSON arrays of `{kind, observed_at_ms, data}`; `fills` also supplies
`wallet`. Supported kinds are public `trade`, `deposit`, exact observed `route`,
`fills` and `coverage_gap`. Scenarios specify `id`, `target_wallet`, `expected_wallets`,
optional `start_ms` and `scan_budget`, and `kind` (`synthetic`, `verified_relationship`,
or an unverified hypothesis). Verified relationships require `verification_refs`;
they still do not prove beneficial ownership. Hidden wallets are not pre-seeded.

Replay uses the live store, selection function, registry and investigation ranker
in disposable isolated storage. It reports observation/selection/enrichment/ranking
misses, retrieval by the cutoff at 5/20, first-investigation latency and gaps. It
does not send alerts or tune thresholds. Synthetic small-wallet, no-funding,
delayed-relay, split-account, retention-gap, future-leakage and noisy-control tests
exercise the pipeline. Their success is not a claim of real-world identification
accuracy. The false-identity-alert count is structural while research promotion is
disabled. `data/quality/latest.json` exposes operational coverage independently of
whether a labelled replay has ever been run.

## Scheduled runtime and persistence

The hourly scan requests this order: restore its checkpoint and import observation
batches; poll up to 12 public markets; index stored routes and resolve at most 20
source messages; enrich candidates; rank successor investigations; publish quality;
compact, snapshot and upload the database; then remove acknowledged older artifacts.
Scan has a 40-minute job backstop and bounded steps. Scheduling is intermittent:
the cron expression is a request, and existing run history shows substantial delays.
There is no claim of complete public-market coverage or always-on hosting.

`scan.yml` is the **only authoritative checkpoint writer**. Watch remains in its
independent `watch-data` concurrency group and trace remains in `data-commit`.
They each upload a uniquely named observation shard from their temporary store,
without restoring or overwriting the scanner's scheduling/cohort state. Scan
imports facts idempotently by event and artifact IDs, at most 40 batches per run.
One corrupt shard remains pending and does not invalidate a valid checkpoint.
Daily analysis restores a checkpoint copy for historical controls and never
publishes it back. Its new CCTP observations go into a separate temporary database
and observation shard for the owner to import.

Observer jobs publish their source cursors only after a successful snapshot and
acknowledged upload. Failed/skipped publication restores only Circle's `last_block`
or CCTP's `cursor_ms` and `forwarder`, retaining unrelated reports and alert receipts.
The next job replays the unsaved range. Authority snapshots, which cannot be replayed
after revocation, enter a JSON retry outbox until a shard durably contains them.

Artifacts are scoped to a hash of the branch name and named by run ID/attempt.
The repository workflow token supplies artifact permissions; no external paid key
is required. Only scan has `actions: write` for cleanup; analysis needs `actions: read`.
Observation shards are deleted only after a newly uploaded checkpoint is confirmed
in the artifact listing. The latest two checkpoints remain; artifacts otherwise
expire after seven days. Listing/import/download/cleanup work is bounded. The
checkpoint has limits of 512 MiB uncompressed and 48 MiB compressed. Repository
artifact quotas can still be exhausted if imports stop; an upload failure is a
visible workflow failure, never permission to buy storage or discard unseen facts.

Scheduled compaction retains at most 100,000 market events within 30 days,
250,000 fills within 90 days globally, and 100,000 generic observations.
Those caps count rows; the checkpoint budget counts bytes, and with every
table at its cap the compressed store reached ~48 MiB, so from 2026-10-03
every snapshot was refused and discovery froze on a three-day-old checkpoint.
The scanner's own checkpoint is therefore taken with `snapshot_within_budget`:
over budget, the oldest 15% of replayable bulk (fills outside active backfills,
generic observations other than authority) is removed and the snapshot taken
again, at most 8 rounds, recorded as `storage_retention.byte_trimmed` and in
`data/discovery/state.json`. Observation shards are never trimmed - their facts
are unseen - so an oversized shard still fails visibly. Explicit
authority can survive up to 730 days subject to that generic row cap. Evaluation
profiles have their own 90-day/10,000-row cap, keeping the first and last profile
per member per UTC day. Frozen membership excludes new nonmembers from retention.
Up to 20 members receive strict order reads per scan within 60 seconds; failures
preserve previous profiles and successful empty reads remain explicitly unsupported.
Wallet aggregates survive market
pruning; history outside these limits is not claimed complete. When fills are
pruned, their coverage claims are invalidated. Standalone collection without a
checkpoint uses the larger 500,000-event cap described above.

`data/discovery/state.json` records restore status, imported/pending/expired
batches, errors and checkpoint preparation. `cold_start` or `state_lost` explicitly
marks absent prior history. A restore error prevents dependent database writers
from running, and the job still publishes its failure report. Successful database
restoration does not mean the subsequent public reads succeeded: the market
summary keeps last attempt, last successful read and last positive observation
separate. Cross-workflow health checks monitor discovery, routes and investigations.

If a restore fails, inspect the scan's artifact error before rerunning. Preserve
both retained checkpoints and pending observation artifacts. Download an intact
checkpoint artifact ZIP to recover locally while no process has the destination
database open:

```powershell
.\.venv\Scripts\python.exe -c "from src.discovery_state import restore_archive; restore_archive('checkpoint.zip', 'data/.local/recovered.sqlite3')"
.\.venv\Scripts\python.exe scripts/collect_market_discovery.py --once --db data/.local/recovered.sqlite3 --output-dir data/.local/recovery-report
```

Restoration validates archive contents and SQLite integrity before replacing a
file. Use a new path for recovery; do not replace a live database. If all artifacts
expired, the JSON candidate registry and collected target files remain, but missing
raw discovery history and the held-out cohort cannot be reconstructed by assertion.
Resume collection and let prospective validation accumulate again. No automated
checkpoint fallback hides loss by silently substituting an older copy.

Sources: [GitHub artifact endpoints](https://docs.github.com/en/rest/actions/artifacts)
and [upload-artifact](https://github.com/actions/upload-artifact).

## Operating the investigation queue

The new Investigations page prioritises observed funding/return routes and authority
connections, then shows position handoffs and observed sessions. These priority
weights are research choices, not calibrated ownership probabilities. Inspect
counterparties and amount/context, especially for unsolicited small transfers or
exchange payouts. An observed return records the immediate transfer source; it
does not reveal the original customer behind a service.

Source-message binding checks the burned token, raw amount and sender relationship,
and requires a unique transfer leg. A matching dollar value or a transaction-level
recipient cannot substitute for this binding in an ambiguous batch.
Both movement accounting and route discovery consume the same transfer-bound
proof. Old transaction-only caches remain clues. Unavailable source messages rotate
behind unattempted transactions, so one missing old receipt cannot monopolize reads.
Blockscout retains its opaque page continuation separately from the numeric forward
cursor. Resumed backfills also sample fresh activity when the budget permits; that
sample never claims complete history or advances past unread older pages.

Use the funding/authority filter for factual connections and the sparse-session
filter for candidates needing more observation. Each row supplies a next check,
confounders, conflicting evidence and raw supporting event references. Unresolved
routes remain a separate queue with the next query. A bridge/exchange classified
as a destination is distinguished from a configured controlled recipient. Missing
reports show “not collected”; an empty list cannot establish that no other wallet
exists. The dashboard reads reports on `main`; a local branch preview does not
publish branch data or silently substitute demonstration leads.

For a manual local cycle, after public collection, run:

```powershell
.\.venv\Scripts\python.exe scripts/index_discovery_routes.py --resolve-sources
.\.venv\Scripts\python.exe src/scanner.py
.\.venv\Scripts\python.exe scripts/check_successor_hypotheses.py
.\.venv\Scripts\python.exe scripts/evaluate_discovery.py --quality-only
.\.venv\Scripts\python.exe scripts/discovery_artifacts.py snapshot --kind state
```

The existing scanner follows configured alert policy; collection, route indexing,
investigation ranking and replay themselves do not send notifications. Local
snapshotting needs no GitHub token and creates `data/.local/artifact/snapshot.sqlite3.gz`.
It does not upload or deploy anything. Install `requirements.txt` for these commands;
the scheduled daily fingerprint rebuild uses `requirements-analysis.txt`.

The most valuable next operational evidence is forward coverage: capture markets
the trader actually trades, keep exploration rotating, investigate small accounts
before their histories roll off, and measure where verified cases are lost in
observation, selection, enrichment or ranking. Add dated verified cases without
changing the held-out controls. More detectors do not compensate for missing
observations or a pipeline that cannot retain and revisit them.

## Throttling and resumable enrichment

Scanner public reads use a 900-second budget and conservative response-weight
pacing (600 weight/minute). Priority work stops taking new wallets after seven
minutes, with a default 80-wallet cap and one exploration slot in five. Leaderboard
work stops taking new wallets after 13 minutes, leaving time for held-out orders
and publishing. These are collection limits; the workflow's step timeout remains
a backstop for unrelated blocking work.

An HTTP 429 ends that batch without repeated retries. `data/scans/latest.json`
publishes `collection.status`, stop reason, read failures, incomplete histories
and per-phase attempted/deferred counts. `wallets_scanned` is the count attempted,
not a claim that each was fully enriched. The quality report and dashboard show
partial coverage. The next run orders wallets by oldest attempt; scanner scheduling
lives only in the authoritative checkpoint and cannot be overwritten by a shard.

Partial fill pages commit observations and their inclusive continuation together.
The default allows six pages: five overlapping 2,000-row responses cannot finish
a full 10,000-fill retained window. A live probe returned more than that documented
retention limit, so only the observed terminal page establishes completion. High
timestamp overlap can also require resumption; saturation is never skipped.
Up to 100,000 of the existing 250,000 cached rows are reserved for whole pending
prefixes, ordered by oldest attempted wallet. Resume and fresh slots alternate
within selection groups, preserving public exploration. Prefixes exceeding the
reservation, or losing rows to the 90-day limit, can still be evicted explicitly.
Only a successful available-history read updates complete coverage. Timestamp
saturation remains unresolved; requesting an older start backfills the missing
prefix. Local/global retention pruning invalidates affected continuation and
coverage claims, so large or old histories can require reacquisition.

Dormancy uses a separate 120-second budget and saves its fair-rotation state in
`data/dormancy/latest.json`. Portfolio history rules out births outside plausible
handoff windows before any trade fetch. Missing portfolio birth is unknown. A
plausible birth still needs observed trades, and older observed trades veto the
apparent birth. Failed/deferred checks retain previous findings marked `stale`;
stale findings do not notify or enter current roster evidence. All handoffs remain
research hypotheses, not ownership proof.
