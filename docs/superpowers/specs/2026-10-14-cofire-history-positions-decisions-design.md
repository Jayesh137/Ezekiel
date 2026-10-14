# Historical co-firing, position notices, and the operator's decision queue

Date: 2026-10-14. Author: Claude (Fable 5.1) on the operator's instruction
"make this project as effective and useful as possible, in any and all ways".
Written as the design for this session's build; the operator approved the
goal, not each item, so every ruling below is recorded with its cost if wrong.

## Why these three, from first principles

The mission is the complete set of his Hyperliquid wallets. After eight months
the set beyond config is empty, and the vectors that find a wallet funded across
a custody gap (no flow link) are behavioural: the execution program (vote gated
closed by the tape ceiling, 17%) and co-firing (built 2026-10-14, evidence only,
zero of his bursts seen). Meanwhile we hold 195,093 of his fills, 92,915 orders
in **296 bursts** since 2026-02-05, and SQD's keyless portal serves every fill
on any coin for any block range back to 2025-10-02. So the one vector that needs
no link between the wallets can be read over his ENTIRE stored history today,
with his own known wallets as positive controls and the whole taker population
as the base rate. That is item 1.

The owner copy-trades manually. The close watch reads his wallets every ~10
minutes and discards the one thing a copy-trader acts on: the book. Item 2 tells
them, at low priority, when any wallet of his opens, closes, flips or resizes a
position.

Two decisions have sat unseen for weeks (`0x160f6ef9…` since 2026-10-14,
`0xb83de012…` since 2026-09-16), each of which would move the roster by itself
if ruled. Item 3 makes the queue visible in the digest and the dashboard and
gives the operator a one-command way to rule.

## 1. Historical co-firing census

`src/cofire_history.py` (pure) + `scripts/census_cofire_history.py` (I/O).

- **Sessions.** His stored taker orders (fills grouped by hash; `crossed` only)
  clustered by a 60 s gap across all coins → sessions. Each session names its
  coins and its [first, last] order time.
- **Window.** Each session is read ±`PAD_MINUTES` (15) around it, filtered to the
  session's coins, from SQD. The padding is what makes `expected` a base rate:
  inside a dense burst every taker co-fires by construction; the window must hold
  off-burst time for the same wallets. A long session is read in slices; the
  cursor is the session index, never a block.
- **Time → block.** SQD answers header-only lines for an empty filter, so a
  block for a timestamp is found by bisection on cheap header reads, cached in
  `data/state/sqd_block_index.json` (monotone checkpoints; interpolation seeds
  the bisection). Strict: an unreadable header raises.
- **Fold.** `cofire.fold_window(state, orders, {target}, now)` per session, with
  "his" = the target ONLY, so his known wallets (`known_self_wallets`) are
  scored like strangers and reported as **positive controls**: if his own second
  wallet co-fires with him, the method is validated on him; if it does not, that
  is written down as "his wallets are not worked together", and the vector's
  reach is bounded accordingly.
- **Base rate.** Over every wallet with ≥`MIN_EVENTS` events: the share that
  clears `is_hit`, and the distribution of lift and lead share (deciles). This is
  the number the casebook's LR table has been waiting for (`cofire` kind =
  "assumed" until measured).
- **Output.** `data/tape/cofire_history.json`: state (same shape as the live
  state), `sessions_read`, `controls`, `base_rate`, `hits`; hits are registered
  as `tape_cofire` candidates exactly as the live screen does, so the per-wallet
  detectors examine them next trace run. Resumable: the session cursor and the
  partial state are written after every session.
- **Where it runs.** Locally in this session for the full history (one-off, ~300
  requests); in `tape.yml` as a bounded step (`--max-sessions 6`) that keeps the
  census current with sessions the live fold may have missed (the feed cursor
  can lag) — idempotent on the session index.

Cost if wrong: a session index drifting (fills re-deduped) re-reads sessions —
the fold is idempotent per burst id only within one state, so re-reads could
double count. Mitigation: session keys are the first order time + coin set, and
a read session is remembered by key, not index.

## 2. Position notices

`src/positions_watch.py` (pure) + a step in `scripts/check_watchlist.py`.

- `book(state)` → `{coin: {size, side, entry, value, leverage}}` from a
  clearinghouse state (perp + every HIP-3 dex, already merged by
  `account_value`).
- `diff(prev, cur)` → changes: `opened`, `closed`, `flipped`, `resized`
  (|Δsize| ≥ 25% of the larger), each with the before/after and notional.
- First reading of a wallet is a baseline (rule: an absent previous record is a
  baseline for a fact about the past).
- Stored in `data/watchlist/positions.json` (watch.yml is the single writer).
- One notice per run at most, through `alerts.send_notice` at **default**
  priority (it is the owner's working signal, so it may sound; the digest stays
  low), covering the target, `known_self_wallets` and roster CONFIRMED wallets.
  Never through `send_alert`: it is not a detection and must not touch delivery
  health or the severity gate. `POSITION_NOTICES=0` disables.

Cost if wrong: noise during a long burst — bounded to one message per watch
run (~10 min), and resizes below 25% are silent.

## 3. The operator's decision queue

- `src/decisions.py`: `pending(config, casebook, roster)` → the open questions
  the system cannot settle itself: (a) casebook top-N unknown cases with money
  both ways with him and no ruling; (b) `watch_wallets` questions older than
  30 days with no ruling; (c) handoff families above a stated p_now. Each has an
  address, a one-line why, and the exact command to rule.
- `scripts/casebook.py rule <address> him|not_him|unsure --note "..."` writes
  `config.casebook_rulings` (and `known_self_wallets` on `him`), so a ruling is
  one command, not a hand edit of config.json.
- The digest gets a "Needs your ruling" line; the dashboard home page gets a
  card listing the queue with the command.

Cost if wrong: an over-long queue is ignored — capped at 5, ranked by p_now.

## Not built, measured or decided this session

- His own fills 2025-10 → 2026-02 by user filter: ~2,000 requests (222K blocks
  per user-filtered request, measured today), too heavy for CI and of moderate
  value; left as a documented local job.
- PnL-series correlation off leaderboard snapshots: only the latest leaderboard
  is kept, so there is no series to correlate.
