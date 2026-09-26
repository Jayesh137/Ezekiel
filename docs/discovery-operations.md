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
