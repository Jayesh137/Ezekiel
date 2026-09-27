# Wallet discovery implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Find the target's other and future active Hyperliquid trading accounts sooner, with reproducible evidence and explicit coverage limits.

**Architecture:** Preserve the existing collectors, scheduled workflows and dashboard. Repair the evidence pipeline, introduce a persistent discovery registry and event index, then use these observations for economic-route reconciliation, broad trade discovery, behavioural retrieval and chronological evaluation. Experimental associations produce investigation leads, not asserted ownership.

**Tech Stack:** Python 3.12, existing requests/numpy, SQLite from the standard library, existing Svelte dashboard and GitHub Actions. A separately installable websocket dependency may be used by an optional continuous collector.

**Spec:** `docs/2026-09-25-wallet-discovery-review.md`, approved by the user's instruction to implement everything and use best judgment. Later instructions require preserving the current branch and uncommitted work and prohibit adding OpenAI API usage.

## Global Constraints

- Work on the existing `improve/wallet-discovery` branch; preserve existing uncommitted work.
- No OpenAI API calls, OpenAI API keys, paid data subscriptions, external messages, money movement or automatic spending.
- Use existing configured public blockchain/Hyperliquid data sources. Free public endpoints are permitted; do not add a new mandatory paid API key.
- Preserve production tracking observations. Tests and replay use temporary paths and mock notification sinks.
- Keep existing JSON/dashboard consumers compatible. New data fields are additive unless fixing a documented incorrect meaning with consumers/tests migrated together.
- Never equate missing/failed/partial data with observed inactivity or confirmed ownership.
- Retain raw observation identifiers and distinguish protocol authority, financial association, behaviour and successor hypotheses.
- Newly introduced research signals cannot independently promote ownership tiers or bypass validation policy.
- High-volume raw discovery data lives in an ignored local SQLite store; publish bounded JSON summaries and persist the store between scheduled runs using an artifact, never by committing its binary to Git.
- Defaults must work with the free scheduled setup. Continuous collection is runnable locally or on an existing host and must report intermittent coverage honestly.
- Implement meaningful regression tests before behaviour changes. Run relevant tests and lint; record actual commands and results in each task report. Full Python and dashboard checks run after integration.
- Workers do not spawn subagents, send notifications, push, deploy, or alter unrelated files. Controller owns reviews and integration decisions.

## Review Focus

1. API failure or a saturated timestamp page must retain last successful evidence and expose a coverage gap; task 3.
2. Duplicate bridge legs and shared-service observations must never become two independent identity votes; tasks 1, 5 and 7.
3. Replayed websocket/archive events must not increase activity counts or move cursors past unpersisted records; task 4.
4. A previously explored recipient forwarding later, or a small new account outside the leaderboards, must remain discoverable; tasks 3 and 4.
5. Sparse histories, changing target regimes and shared bot defaults must not create falsely certain matches; tasks 2, 6 and 7.

### Task 1: Reconcile economic exits and preserve correlation alternatives

**Files:** Create `src/movements.py`, `tests/test_movements.py`; modify `src/correlator.py`, `src/accounting.py` and directly affected tests.

**Interfaces:** `reconcile_movements(records: list[dict], ledger: list[dict], cluster: set[str], bridge_decodes: dict, min_amount: float = 0) -> dict` returns `movements`, `unresolved_exits`, `resolved`, and `coverage`. Each movement preserves event IDs, source refs, amounts, time, actual endpoints, resolution and evidence; callers can handle absent decodes. Keep `collect_target_exits` and `find_correlations` backwards compatible.

- [ ] Add failing regression tests: four known bridge self-deposit routes and known-self transfer fixtures do not seed unresolved matches; a known treasury's onward external transfer still does; unrelated bridge decoding never resolves an exit; an HL withdrawal plus identified payout is counted once; unknown prices do not become zero amounts.
- [ ] Add rejected-approximate-then-exact correlation regression, permutation-invariance tests and an ambiguous-match test. The $975K rejected deposit must not consume the $1M exit before a valid $1M deposit.
- [ ] Implement economic reconciliation using exact event/bridge relationships where available and conservative unresolved status otherwise. Read the complete configured trusted cluster, not inferred ownership. Do not infer exact leg relationships merely from amount similarity.
- [ ] Generate acceptable correlation hypotheses before assignment; make one-to-one selected matches deterministic, prefer stronger evidence globally and retain bounded alternative/ambiguity metadata. A small exact maximum-weight assignment is acceptable, with bounded scaling. Keep scores labelled heuristic, not probability. Guard NaN, nonpositive tolerances and malformed inputs.
- [ ] Publish unresolved economic routes and separate accounting totals for classified first destination, exact route resolution and unresolved custodial destination. Preserve legacy keys for consumers with clarified labels.
- [ ] Run movement, correlation, accounting, withdrawal and continuity tests; run lint on changed files; document commands and results; commit only task-owned changes.

### Task 2: Restore feature parity and honest validation

**Files:** Modify `src/fingerprint.py`, `src/scanner.py`, `src/backtest.py`, `src/thresholds.py`, `src/roster.py`, `src/calibration.py`; add focused regression tests and update directly affected tests.

**Interfaces:** Preserve existing fingerprint functions; extend optional inputs where necessary. Both recent and full fingerprints must use one order-profile implementation. Validation outputs include cohort size, feature availability, as-of coverage and schema/version provenance; consumers tolerate legacy files.

- [ ] Regress the actual production path: `build_fingerprint_recent` yields a supported nonempty `order_profile` from orders in the relevant window, and scanner comparisons can use it. Orders after the observation cutoff are excluded.
- [ ] Normalize order lifecycle by account/order ID and latest valid observation, so open then filled is one filled order and zero cancellations; rejected/open orders are not cancelled. Unknown statuses and absent orders remain unknown.
- [ ] Fix empty `order_profile` summaries in negative-cohort enrichment. Backtest must fail closed with insufficient negative samples, missing independent as-of state, or unsupported comparisons; do not fabricate historical leverage from current positions. Keep available non-position comparisons usable.
- [ ] Replace the roster's literal behavioural threshold with the same resolved threshold/validation contract used by scanning. Bump relevant feature schema and prevent old-version scores from voting as new evidence.
- [ ] Store wallet ID, feature version, time and feature mask with calibration observations; repeated scans of one wallet are not an independent negative population. Preserve backward read compatibility while marking unknown cohorts.
- [ ] Run feature, order, backtest/validation, threshold, stale-scorer and roster tests and changed-file lint. Record results and commit task-owned changes.

### Task 3: Candidate registry, reliable histories and recurring surveillance

**Files:** Create `src/candidate_registry.py`, `src/history.py` and corresponding tests; modify `src/utils.py`, `src/scanner.py`, `src/roster.py`, `src/transfer_graph.py`, relevant discovery producers and tests.

**Interfaces:** `iter_candidates(data_dir: Path | None = None) -> list[dict]` reads all individual candidate records with legacy fallback. `observe_candidate(wallet: str, evidence: dict, data_dir: Path | None = None) -> dict` preserves discovery sources, freshness, activity and scheduling; must not overwrite older stronger factual evidence with a failed read. `fetch_fill_history(wallet: str, start_ms: int, end_ms: int, fetch=None, max_pages: int = 5) -> dict` returns fills, status, coverage, saturation, gaps and error. An explicit `hl_read` result preserves success/error distinctions while `hl_post` remains compatible for unchanged callers.

- [ ] Regress score downgrades and candidates beyond the top 50: every checked existing candidate updates its successful latest outcome, and roster evidence reads the complete registry independently of display ranking.
- [ ] Add paginated available fill collection, overlap deduplication using wallet/coin/time/tid, deterministic ordering, bounded requests and retention-limit metadata. Handle identical timestamps filling a page without falsely advancing through missing events. Persist candidate histories incrementally without committing unbounded raw data.
- [ ] Distinguish last attempted read, last successful read, last scored and last positive evidence. A failed read retains successful history and does not create inactivity or demotion. Test partial pages followed by errors.
- [ ] Split graph historical completion from incremental refresh. Already-expanded relevant recipients become due again using observed activity, balances/retained funds and elapsed time, with bounded work and fairness. Legacy ledgers migrate without resweeping every address at once. Reopen coverage after decoder/schema changes.
- [ ] Remove permanent exclusion of small/new candidates at the cheap discovery stage. Keep expensive-enrichment and alert budgets separate. Ensure direct contacts and research discoveries can join the same registry.
- [ ] Run pagination, scanner, roster, graph/frontier/watch tests and new tests; lint changed files. Document interfaces and commit.

### Task 4: Broad market discovery and durable event ingestion

**Files:** Create `src/discovery_store.py`, `src/market_discovery.py`, `scripts/collect_market_discovery.py`, `tests/test_market_discovery.py`, `tests/test_discovery_store.py`, `requirements-stream.txt`; modify scanner candidate selection and `.gitignore` as needed.

**Interfaces:** `DiscoveryStore(path: str | Path)` uses SQLite and provides `ingest_trades(trades: list[dict], observed_at_ms: int) -> dict`, `candidates(limit: int = 500, now_ms: int | None = None) -> list[dict]`, `coverage() -> dict`, `export_summary() -> dict`, and context manager/close. `collect_once(config: dict, store: DiscoveryStore, fetch=None, now_ms: int | None = None) -> dict` uses bounded public recent-trade polling. CLI supports `--once`, `--stream`, `--import-jsonl PATH`, `--db PATH`, `--output-dir PATH`, and bounded runtime/market options; no paid API or OpenAI integration.

- [ ] Ingest both addresses from actual Hyperliquid trade schema (`users`, `coin`, `time`, `tid`, price/size and side). Validate addresses and numeric values, exclude configured self/infrastructure from candidate promotion but retain observations. Deduplicate market/time/trade identity across reconnects, overlapping polls and archives; self-trades must not double-count an account's involvement.
- [ ] Persist events and progress atomically, record observed intervals and gaps, avoid claiming that a recent-trades poll covers unobserved time. Use WAL/busy timeout and bounded retention/compaction with aggregate first/last-seen preserved. Cover corrupt/malformed rows without losing valid rows.
- [ ] Implement free bounded polling plus optional continuous websocket collection using verified public schemas. Subscribe to target markets and rotating exploration markets, including discovered HIP-3 markets. Reconnect with bounded backoff, persist before cursor progress, recover available overlap and mark irrecoverable gaps. Enforce limits; no busy loop or unbounded buffers.
- [ ] Support local JSONL trade/archive import with streaming reads, provenance and idempotency. Do not automatically download paid/requester-pays archives.
- [ ] Feed discovered active accounts into scanner scheduling with a reserved exploration allocation; a small non-leaderboard account is eligible before its balance reaches $1M. Public trading involvement is discovery, not identity evidence.
- [ ] Produce bounded discovery/coverage summaries and installation/run instructions; test lifecycle/reconnect using fakes and real SQLite. Run relevant tests/lint; commit.

### Task 5: Bridge origin index, route follow-ups and historical authority

**Files:** Create `src/route_index.py`, `src/authority_history.py`, `scripts/index_discovery_routes.py`, tests; modify `src/circle_flows.py`, `scripts/check_circle_flows.py`, `src/agent_links.py`, chain readers and bridge consumers as needed.

**Interfaces:** `index_routes(records: list[dict], bridge_decodes: dict, circle_events: list[dict], cluster: set[str]) -> dict` returns verified route links, unresolved routes and funding-to-HL endpoint discoveries. `index_authority(events: list[dict], as_of_ms: int | None = None) -> dict` returns validity intervals and shared-authority leads with source event IDs. Both are pure, with file/store I/O in the script.

- [ ] Separate original funder, transaction caller, protocol message sender, relayer and actual HL recipient. Join source/destination only by validated protocol identifiers or exact source decodes. Contract `messageSender` must not be labelled the originating person.
- [ ] Preserve decoded non-cluster Circle events in the ignored discovery store for later joins, with bounded published summaries. Retain low-value legitimate deposits in cheap discovery; guard spam/forged tokens and address poisoning.
- [ ] Add verified free chain-reader fallback for Base/Optimism where supported by existing Blockscout capabilities, with pagination, incomplete status and canonical token validation. Other unsupported chains stay explicitly unsupported unless a validated free adapter is implemented; no claimed complete coverage from balance-only reads.
- [ ] Build an inverse treasury-to-HL funding view and a machine-readable unresolved-route queue carrying boundary, last exact event, missing evidence and next useful query. Repeated/return private routes are association leads; public routers do not propagate ownership.
- [ ] Index supported historical approve/revoke/subaccount/multisig observations from explicit local action import and existing collectors. Preserve validity intervals, revocation and time-bounded overlap; no fictitious global action websocket. Reused operators produce authority evidence, not automatic same-owner confirmation.
- [ ] CLI accepts recorded actions/route inputs and current stored observations; exports discovered candidates through task 3 registry. Test route ambiguity, revoked agents, shared routers, malformed input and idempotency. Run relevant tests/lint; commit.

### Task 6: Trading episodes and creative successor hypotheses

**Files:** Create `src/episodes.py`, `src/successor_hypotheses.py`, `scripts/check_successor_hypotheses.py`, tests; modify `src/fingerprint.py`, `src/scanner.py`, `src/comovement.py` and collectors only as needed.

**Interfaces:** `build_episodes(fills: list[dict], orders: list[dict] | None = None) -> list[dict]`; `episode_profile(episodes: list[dict]) -> dict`; `compare_episode_profiles(target: dict, candidate: dict) -> dict`; `find_successor_hypotheses(target: dict, candidates: list[dict], context: dict | None = None) -> list[dict]`. Outputs include coverage, independent episode/session counts, explanatory features and parent observations.

- [ ] Reconstruct orders/slices and position transitions by reliable IDs and time; distinguish censored episodes. Compute capital-normalized size/rounding, pacing, pause/restart, entry/exit ladders and basket transitions where supported. Do not infer unknown intent or drawdowns without observed starting state.
- [ ] Compare multiple observed target regimes and early-life windows, preserving individual similarities and information level. Add episode evidence as an explicitly experimental retrieval dimension until task 7 validates promotion use. Do not turn similarity into ownership probability.
- [ ] Implement position handoff detection using quantity/exposure changes with market/common-event controls; reactivation/change-of-style signals; return-route corroboration; and small-group book aggregation only for independently linked groups.
- [ ] Implement supported operational sequence and authority/treasury corroboration from task 5 observations. Public disclosure ingestion is manual, dated, structured hypothesis input only; unconfirmed identities never enter ground truth.
- [ ] Improve co-movement to use one-to-one episodes and multiple session-preserving null shifts, requiring adequate independent decisions. Provide copier-cohort transition research output only when a following relationship can first be demonstrated; otherwise explicit insufficient-data status.
- [ ] An investigation report ranks by evidence/information and lists contradictions, confounders and the next measurement. Strong factual links remain visible despite style changes. Fixtures test partial fills, shared software, news synchrony, split accounts, sparse histories and later return links.
- [ ] Run episode/hypothesis/comovement/style tests and lint; record results and commit.

### Task 7: Evidence independence and chronological discovery evaluation

**Files:** Create `src/evidence.py`, `src/discovery_evaluation.py`, `scripts/evaluate_discovery.py`, tests/fixtures; modify `src/roster.py`, `src/continuity.py`, calibration/report producers as needed.

**Interfaces:** `aggregate_evidence(observations: list[dict]) -> dict` groups shared parent event IDs into dependent families and separately reports protocol/financial/behaviour/successor assertions. `evaluate_replay(events: list[dict], scenarios: list[dict], cutoff_ms: int | None = None) -> dict` produces stage-specific discovery recall, top-5/top-20 recall, latency, false-alert counts, coverage and caveats. CLI works offline with fixtures and explicitly selected real recorded inputs.

- [ ] Prevent the same transfer reported by `transfer` and `hl_native` from counting as independent corroboration. Legacy evidence lacking parent IDs is grouped conservatively, not automatically treated as independent.
- [ ] Preserve configured trusted seeds and explicit protocol relationships; shared delegates, common CEXs, behaviour and private-address associations do not individually mean confirmed beneficial ownership. Update tier explanations and directly affected tests/consumers coherently.
- [ ] Add chronological hide-known-wallet and pseudo-migration scenarios; lower capital, no direct funding, delayed relays, split accounts, retention gaps and noisy controls. Prevent future feature/account state leakage. Keep synthetic scenarios separate from real verified relationships and from production candidates.
- [ ] Evaluate the real candidate-ingestion/scheduling/ranking interfaces rather than a disconnected scoring demo. Attribute misses to observation, selection, enrichment or ranking. Assert a small non-leaderboard account and delayed relay reach investigation in end-to-end replay.
- [ ] Use fixed held-out cohorts, feature masks/schema versions and independent-wallet/session counts. Report insufficient evidence honestly, with no automatic threshold tuning against the test set. Research promotion remains gated until meaningful held-out criteria are met.
- [ ] Publish quality/coverage and route-queue metrics usable by the dashboard. Replay notification sinks are mock-only. Run relevant tests/lint; commit.

### Task 8: Runtime integration, operator visibility and final verification

**Files:** Modify `.github/workflows/scan.yml`, `.github/workflows/trace.yml`, `.github/workflows/watch.yml`, add a bounded discovery workflow if needed, `config.json`, `src/feed_health.py`, dashboard API/data views/tests, `README.md`; add `docs/wallet-discovery-operations.md` and integration tests.

**Interfaces:** Existing reports remain readable. New published `data/discovery/latest.json`, `data/investigations/latest.json`, `data/discovery_evaluation/latest.json` (where actually evaluated) are optional/freshness-aware; absence is shown as not collected. Binary/raw stores are restored/persisted through bounded workflow artifacts with explicit loss/coverage status.

- [ ] Wire completed collectors, route index and hypothesis generation into sensible scheduled order with independent failure reporting, budgets and no mandatory new paid credentials. Avoid adding duplicate full scans to every watch. Resolve artifact races/retention explicitly; use one designated writer for continuous/discovery state.
- [ ] Add minimal operator views for discovered accounts, evidence reasons, unresolved routes, last successful observations, gaps and evaluation status; preserve design system and mobile usability. Clarify accounting labels so classified infrastructure is not presented as identified future wallets.
- [ ] Document exact free polling, optional local continuous streaming, JSONL import and replay commands; required runtime/dependencies, state location, storage retention, recovery and realistic coverage. Provide an operational run mode that works now without a paid service. Do not claim deployed always-on hosting unless actually running and verified.
- [ ] Run a read-only bounded public-data smoke test when network permits, recording actual observed schema/coverage without notifications or modifying historical production data. If the environment blocks network, request the appropriate tool escalation and retain an explicit unverified-live limitation if unavailable.
- [ ] Run the full Python suite, ruff, dashboard unit tests/build/PWA check, relay tests, workflow/integration checks and repository size/diff checks. Investigate relevant failures, preserve existing user work, and capture baseline versus introduced issues honestly.
- [ ] Produce a final implementation/operations report stating what is enabled, what needs continuous hosting/data, evidence quality limits and exact test results. Commit task-owned work; controller obtains an independent whole-branch review and addresses findings before final delivery. Do not push/deploy without an explicit authorized deployment step.
