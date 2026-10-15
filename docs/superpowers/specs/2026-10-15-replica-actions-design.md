# Venue-wide action history and treasury-session co-occurrence — design

Date: 2026-10-15. Status: implementing on `feat/replica-actions`.

## Why

After eight months the set beyond the config cluster is empty, and every vector
that needs a connection between his wallets has answered a measured no (agents,
sub-accounts, referrals, L1 neighbourhood, co-firing over all 294 sessions). The
remaining ways a second wallet of his can exist are a custody gap (funded from
the same exchange account) and a wallet he simply never connects to this one.
Both leave two things visible venue-wide: *what he set up* (agents, builders,
referrers, Circle recipients — only the explorer's 300-action window ever showed
these, and it rolls a payload out within a day) and *when he does treasury
work* (a person managing two accounts tops up, withdraws and re-approves agents
in one sitting; his FTX screenshots show exactly that habit).

Measured 2026-10-15: SQD's keyless portal serves `hyperliquid-replica-cmds` —
**every L1 action on Hyperliquid, with its signing user, action payload, nonce,
vaultAddress and ok/err status**, from block 868,700,000 (2026-01-21 16:40 UTC)
to a head ~10 days behind the chain (not real-time). Filters: `user`,
`actionType`, `vaultAddress`, `status` (a single string; a list is refused).
A user-filtered request covers ~2,300 blocks (~3 min of chain) in ~0.6 s
regardless of how many users it names or how wide a range it asks. **Measured
later the same day:** ~40% of requests are refused 529 at any concurrency and
the same shape is served seconds later (narrowing buys nothing); six workers
re-asking at once drew 429; total throughput is ~1 served request/s per client
however many workers run. A full user-filtered sweep of the dataset is
therefore ~30 hours, so the history backfill runs as bounded CI increments
(newest-first, ~4.8M blocks/day against 1.2M/day of new chain) instead of at
once. Any 20–30 minute window holds 400–1,200 accounts with a treasury-kind
action, so the hit bar's p was tightened from 1e-3 to 1e-4. An `actionType` read over 2,900 blocks returned 39,607 actions (38,618
`noop`, 335 `setReferrer`, 155 `sendAsset`, 76 `approveAgent`, 69 `spotSend`,
25 `usdSend`, 9 `sendToEvmWithData`, 5 `withdraw3`) in 0.9 s. Failed actions
(`status: err`) are included.

## What is built

### 1. `src/replica_cmds.py` — strict client

Copy of `fills_feed`'s shape: `FeedError` on any non-answer, a cursor that never
skips an unread range, overload (529) handled by narrowing then waiting,
bounded by requests/bytes. Adds `block_at(ts_seconds)` through the portal's
`/timestamps/{ts}/block`, a `user`/`actionType` selector, and
`as_explorer_row()` so `hl_actions.own_actions` parses a replica row unchanged
(`hash` = `replica:<block>:<index>`, `error` = `"err"` when the status was not
ok). Rows carry `block`, `time` (block timestamp ms), `nonce`, `vault_address`,
`status`.

### 2. `scripts/sweep_replica_actions.py` — his world's complete action history

One writer of `data/replica/`. The user list is **everything the project calls
his world**: the config cluster, `known_self_wallets`, every perimeter member,
`watch_wallets`, and roster CONFIRMED/PROBABLE wallets — one request covers
them all at the same cost as one. Rows are stored in `data/actions/<wallet>/`
through `hl_actions.record` (same shape as the explorer's rows, so every reader
of that store sees them), the cursor in `data/replica/state.json`
(`swept_to`, never past an unread range), and a report in
`data/replica/latest.json`: per wallet, counts by type and status, every agent
ever approved (address, name, first/last seen, signature chain id), builders,
referral codes, Circle recipients by chain, destinations outside the perimeter,
and failed actions (an intended destination is evidence too).

`--backfill` sweeps from the dataset start with N workers over fixed segments,
resumable (completed segments recorded in the state file); the default run is
incremental and budgeted (`--max-requests`, `--time-budget`) for `trace.yml`.

**Alerting policy:** the sweep pages nothing the live detectors already cover
(the collector alerts foreign destinations from the explorer within minutes;
replaying them ten days later is noise). It pages one thing nobody covers: a
historical agent of his whose CURRENT owner (`userRole`) is another account —
an agent key reused by a second account is his (CRITICAL through
`alert_explicit_link`, kind `agent_reused`). Destinations outside the perimeter
are registered as candidates (`candidate_registry`, source `replica_actions`)
so the per-wallet detectors examine them; nothing here mints a vote.

### 3. `src/treasury_sessions.py` + `scripts/census_treasury_sessions.py` — who does treasury work when he does

Sessions: the TARGET's own non-trading actions (replica rows: sends,
withdrawals, Circle sends, class transfers, agent/builder approvals, staking;
plus the target's ledger `deposit` rows, which are L1 events the action stream
does not carry) clustered by a 10-minute gap, from 2026-01-21. His known
wallets are NOT folded in as "him": they are measured like strangers, so they
are **positive controls** — the vector is validated only if it finds them.

Per session: window = [first − 10 min, last + 10 min]; read venue-wide actions
of the treasury kinds (`usdSend`, `spotSend`, `withdraw3`, `sendToEvmWithData`,
`usdClassTransfer`, `subAccountTransfer`, `vaultTransfer`, `approveAgent`,
`cDeposit`, `cWithdraw`, `tokenDelegate`, `sendAsset`), status ok. A user
counts once per session, and only for actions that do not touch him: a user he
paid in that session, or whose action paid a cluster wallet, is a transfer the
ledger already shows, not co-occurrence. Users with more than 20 actions in a
window are bots and are dropped for that window. Two control windows per session
(the same span shifted −7 and +7 days, clipped to the dataset) give each user's
base rate.

Score per user: `sessions_with`, `controls_with`, `lift` =
(sessions_with / sessions) / ((controls_with + 0.5) / (controls + 1)), and a
binomial tail `p`. **Hit:** sessions_with ≥ 4, lift ≥ 5, p ≤ 1e-3. Hits register
as candidates (source `treasury_cooccurrence`); the census is evidence, not a
vote (rule 4: nothing to calibrate against until a known wallet reads as a hit).
State in `data/replica/treasury_census.json`, resumable by session key, bounded
per run (`--max-sessions`, `--time-budget`) in `tape.yml`.

### 4. Wiring

`trace.yml`: "Sweep his world's actions (SQD replica)" before the identity step.
`tape.yml`: "Fold his treasury sessions into the co-occurrence census".
`deploy/vm/jobs.py` mirrors both. Digest gets one line each; the Tripwires page
gets one card each. Casebook: no new kind (evidence enters through the registry
and the detectors it pins).

## Rulings taken without the operator (each with its cost if wrong)

- Session gap 10 min, window pad 10 min, ±7-day controls, bot bar 20 actions:
  a tighter bar misses a slow operator, a looser one admits more chance.
- The sweep never pages a foreign destination (the collector does, live): if the
  collector ever misses one, the replica report shows it ten days later without
  a buzz — the digest line names the count of unregistered outsiders.
- Known wallets as positive controls rather than as "him": a session he shares
  with the treasury is counted for the treasury, which is the point.

## Not built, and why

- Signature recovery (the signer behind each action): needs an exact msgpack
  reconstruction of every action; fragile. `approveAgent` payloads already name
  agents, and `userRole` names an agent's owner.
- A venue-wide `approveAgent` naming-family sweep: ~32K approvals a day,
  ~3 GB over the dataset; worth a one-off local run once the sweep above shows
  his own agent names follow a template. Recorded as a remaining opportunity.
- Replacing Blockscout's Bridge2 reads with SQD's `arbitrum-one` logs (keyless,
  complete, no index holes): measured to work (Bridge2 logs by address filter);
  a separate change to `src/boundary`.
