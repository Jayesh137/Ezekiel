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
candidates by default (200 maximum), rotates oldest investigations within source
priority, and publishes bounded references plus original counts. Scheduled wiring
and cache persistence are described with the workflow integration below.
