# Ezekiel: Hyperliquid Trader Intelligence & Fingerprinting System

## Product Requirements Document (PRD)

---

## 1. Overview

**Ezekiel** is an automated intelligence system that comprehensively logs every action performed by a specific Hyperliquid trader (codename: **"Ezekiel"**), builds a behavioral fingerprint from the data, and uses that fingerprint to identify the trader if they migrate to a new wallet.

**Target Wallet:** `0x45d26f28196d226497130c4bac709d808fed4029`

**Trader Codename:** Ezekiel

**Suspected Identity:** Possibly GCR (Twitter: @GiganticRebirth, @GCRClassic) — **unconfirmed hypothesis**. The system does NOT assume wallet ownership. The wallet and Twitter accounts are tracked as **separate entities** with correlation analysis to test the hypothesis.

**Twitter Accounts of Interest:**
- `@GiganticRebirth` (primary)
- `@GCRClassic` (secondary)

---

## 2. Problem Statement

The user copy-trades a specific Hyperliquid wallet manually. If this trader closes the wallet and opens a new one, the user loses the ability to follow their trades. Hyperliquid's API only returns the most recent ~2000 entries per endpoint, and historical data disappears over time. Without continuous recording, this data is permanently lost.

---

## 3. Goals

| Priority | Goal |
|----------|------|
| **P0** | Permanently capture and store ALL historical and ongoing trading activity for the target wallet before data expires |
| **P0** | Build a multi-dimensional behavioral fingerprint from collected data |
| **P0** | Trace fund flows when withdrawals occur to directly find linked wallets |
| **P1** | Scan Hyperliquid for new wallets matching the behavioral fingerprint |
| **P1** | Send email alerts on fund movements and high-confidence wallet matches |
| **P2** | Ingest existing research documents (GCR.docx, Trade Reviews.pdf) into the trader profile |

---

## 4. Non-Goals

- Copy-trading automation (user trades manually)
- Dormancy alerts (user monitors the wallet themselves)
- Real-time WebSocket streaming (GitHub Actions uses polling; 5-min intervals are sufficient)
- Paid services or infrastructure (everything must be free)

---

## 5. Architecture

### 5.1 Infrastructure

| Component | Service | Cost |
|-----------|---------|------|
| Compute | GitHub Actions (public repo, cron schedules) | Free |
| Storage | Git repo (JSON files organized by date) | Free |
| Email | Brevo SMTP (300 emails/day free) | Free |
| Fund Tracing | Etherscan V2 API (Arbitrum, chainid=42161) | Free (5 calls/sec) |
| Leaderboard | Hyperliquid stats endpoint | Free |

### 5.2 Project Structure

```
Ezekiel/
├── .github/
│   └── workflows/
│       ├── collect.yml              # Cron: every 5 min — poll all HL endpoints
│       ├── backfill.yml             # Manual trigger — initial historical data pull
│       ├── scan.yml                 # Cron: every 1 hour — scan for matching wallets
│       ├── analyze.yml              # Cron: daily — rebuild fingerprint + report
│       └── trace.yml                # Cron: every 15 min — check for fund movements
├── src/
│   ├── collector.py                 # Data collection from Hyperliquid API
│   ├── backfill.py                  # Historical data backfill (fills, funding, ledger)
│   ├── fingerprint.py               # Behavioral fingerprint computation
│   ├── scanner.py                   # Wallet similarity scoring against leaderboard
│   ├── tracer.py                    # Arbitrum L1 fund flow tracing
│   ├── alerts.py                    # Email alert system via Brevo SMTP
│   ├── profile_builder.py           # Ingests research/ docs into profile
│   ├── twitter_monitor.py           # Fetches new tweets via RSS, extracts trading signals
│   ├── twitter_correlator.py        # Correlates tweets vs wallet trades, computes confidence
│   └── utils.py                     # Shared helpers (API calls, file I/O, dedup)
├── data/
│   ├── positions/                   # Position snapshots: YYYY-MM-DD/HH-MM.json
│   ├── fills/                       # Trade fills: YYYY-MM-DD.json (append-only)
│   ├── orders/                      # Open + historical orders: YYYY-MM-DD.json
│   ├── funding/                     # Funding payments: YYYY-MM-DD.json
│   ├── ledger/                      # Deposits/withdrawals/transfers: YYYY-MM-DD.json
│   ├── account/                     # Account state snapshots: YYYY-MM-DD/HH-MM.json
│   ├── subaccounts/                 # Subaccount checks: latest.json
│   ├── vaults/                      # Vault deposit data: latest.json
│   ├── fees/                        # Fee tier snapshots: YYYY-MM-DD.json
│   ├── referral/                    # Referral data: latest.json
│   ├── rate_limit/                  # Volume + rate limit snapshots: YYYY-MM-DD.json
│   ├── l1_transactions/             # Arbitrum on-chain tx data: YYYY-MM-DD.json
│   ├── scans/                       # Scanner results: YYYY-MM-DD.json
│   ├── twitter/                     # Tweet data + correlation analysis
│   │   ├── tweets/                  # New tweets by date: YYYY-MM-DD.json
│   │   ├── archive/                 # Historical backfill per account
│   │   └── correlation/             # Correlation analysis: latest.json
│   └── state/                       # Cursor/checkpoint files for pagination
│       ├── last_fill_time.txt       # Timestamp of last collected fill
│       ├── last_funding_time.txt    # Timestamp of last collected funding
│       └── last_ledger_time.txt     # Timestamp of last collected ledger entry
├── research/                        # User's existing research docs
│   ├── GCR.docx
│   └── Trade Reviews.pdf
├── profile/
│   ├── fingerprint.json             # Computed behavioral fingerprint
│   ├── trader_profile.json          # Aggregated trader profile (from research + data)
│   └── similarity_weights.json      # Configurable weights for fingerprint matching
├── reports/
│   └── daily/                       # Daily summary reports: YYYY-MM-DD.md
├── config.json                      # Configuration (wallet address, thresholds, email)
├── requirements.txt                 # Python dependencies
└── PRD.md                           # This document
```

### 5.3 Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     GitHub Actions (24/7)                        │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │ collect  │  │  trace   │  │   scan   │  │   analyze    │   │
│  │ (5 min)  │  │ (15 min) │  │ (1 hour) │  │   (daily)    │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │
│       │              │             │               │            │
│       ▼              ▼             ▼               ▼            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    data/ (JSON files)                    │   │
│  │  Positions, Fills, Orders, Funding, Ledger, Account,    │   │
│  │  L1 Transactions, Scan Results                          │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │                                       │
│                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              profile/fingerprint.json                    │   │
│  │  Behavioral fingerprint rebuilt daily from ALL data      │   │
│  └──────────────────────┬──────────────────────────────────┘   │
│                         │                                       │
│                         ▼                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   Email Alerts                           │   │
│  │  - Fund movement detected                                │   │
│  │  - High-similarity wallet found                          │   │
│  │  - Daily summary report                                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Data Collection Specification

### 6.1 Hyperliquid API Endpoints

**Base URL:** `https://api.hyperliquid.xyz/info` (POST, Content-Type: application/json)

**CRITICAL:** The API returns max ~2000 entries per call. We MUST use time-based pagination (`userFillsByTime`, `userFunding` with `startTime`/`endTime`) to capture ALL historical data and never lose any records.

#### Endpoints Polled Every 5 Minutes

| # | Type | Request Body | Data Captured | Storage |
|---|------|-------------|---------------|---------|
| 1 | `clearinghouseState` | `{"type":"clearinghouseState","user":"0x45d2...4029"}` | Open positions, leverage, entry price, liquidation price, margin, PnL, account value | `data/positions/` |
| 2 | `openOrders` | `{"type":"openOrders","user":"0x45d2...4029"}` | Active orders: coin, price, side, size, timestamp | `data/orders/` |
| 3 | `frontendOpenOrders` | `{"type":"frontendOpenOrders","user":"0x45d2...4029"}` | Orders with trigger conditions, order type, original size | `data/orders/` |
| 4 | `userFillsByTime` | `{"type":"userFillsByTime","user":"0x45d2...4029","startTime":<last_ts>}` | New trade fills since last check: coin, side, px, sz, fee, closedPnl, dir, crossed | `data/fills/` |
| 5 | `historicalOrders` | `{"type":"historicalOrders","user":"0x45d2...4029"}` | Completed/cancelled orders with status | `data/orders/` |
| 6 | `spotClearinghouseState` | `{"type":"spotClearinghouseState","user":"0x45d2...4029"}` | Spot token balances | `data/account/` |

#### Endpoints Polled Every 15 Minutes

| # | Type | Request Body | Data Captured | Storage |
|---|------|-------------|---------------|---------|
| 7 | `userFunding` | `{"type":"userFunding","user":"0x45d2...4029","startTime":<last_ts>}` | Funding payments: coin, rate, size, usdc amount | `data/funding/` |
| 8 | `userNonFundingLedgerUpdates` | `{"type":"userNonFundingLedgerUpdates","user":"0x45d2...4029","startTime":<last_ts>}` | Deposits, withdrawals, transfers, liquidations | `data/ledger/` |
| 9 | `userFees` | `{"type":"userFees","user":"0x45d2...4029"}` | Fee schedule, tier, volume, discounts | `data/fees/` |
| 10 | `userRateLimit` | `{"type":"userRateLimit","user":"0x45d2...4029"}` | Cumulative volume (cumVlm), request usage | `data/rate_limit/` |

#### Endpoints Polled Every Hour

| # | Type | Request Body | Data Captured | Storage |
|---|------|-------------|---------------|---------|
| 11 | `subAccounts` | `{"type":"subAccounts","user":"0x45d2...4029"}` | Linked subaccounts (directly reveals related wallets) | `data/subaccounts/` |
| 12 | `userVaultEquities` | `{"type":"userVaultEquities","user":"0x45d2...4029"}` | Vault deposits: vault address, equity amount | `data/vaults/` |
| 13 | `referral` | `{"type":"referral","user":"0x45d2...4029"}` | Referral chain, rewards, referred accounts | `data/referral/` |
| 14 | `portfolio` | `{"type":"portfolio","user":"0x45d2...4029"}` | Historical account value and PnL curves | `data/account/` |

### 6.2 Arbitrum L1 Fund Flow Tracing

**API:** Etherscan V2 (works for Arbitrum with chainid=42161)
**Base URL:** `https://api.etherscan.io/v2/api?chainid=42161`
**Hyperliquid Bridge Contract:** `0x2df1c51e09aecf9cacb7bc98cb1742757f163df7`
**USDC Contract (Arbitrum):** `0xaf88d065e77c8cc2239327c5edb3a432268e5831`

#### Endpoints Polled Every 15 Minutes

| # | Action | URL | Purpose |
|---|--------|-----|---------|
| 1 | `txlist` | `?module=account&action=txlist&address=0x45d2...4029&startblock=0&endblock=99999999&sort=desc&offset=100` | All normal transactions |
| 2 | `tokentx` | `?module=account&action=tokentx&address=0x45d2...4029&startblock=0&endblock=99999999&sort=desc&offset=100` | All ERC-20 token transfers (USDC) |
| 3 | `txlistinternal` | `?module=account&action=txlistinternal&address=0x45d2...4029&startblock=0&endblock=99999999&sort=desc&offset=100` | Internal transactions |

**Fund Tracing Logic:**
1. Detect any new USDC transfer OUT from the tracked wallet
2. Record the destination address
3. Check if destination address deposits into Hyperliquid bridge (USDC transfer to `0x2df1c51e...163df7`)
4. If yes: this is likely the new wallet — flag with HIGH confidence alert
5. If no: follow up to 2 hops (destination sends to another address which deposits to HL)

### 6.3 Leaderboard Scanning

**Leaderboard URL:** `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard`
**Additional source:** HyperTracker by CoinMarketMan (free, public wallet cohort data)

The scanner pulls leaderboard wallet addresses, then queries `clearinghouseState` and `userFillsByTime` for each candidate to compare against the Ezekiel fingerprint.

---

## 7. Behavioral Fingerprint Specification

The fingerprint is a JSON object computed daily from ALL collected data. Each dimension captures a pattern that is hard for a trader to consciously change.

### 7.1 Fingerprint Dimensions

```json
{
  "version": "1.0",
  "computed_at": "2026-02-19T00:00:00Z",
  "data_range": {
    "first_fill": "2024-xx-xxTxx:xx:xxZ",
    "last_fill": "2026-02-19Txx:xx:xxZ",
    "total_fills": 12345,
    "total_days_active": 400
  },

  "asset_preferences": {
    "description": "Which coins traded and relative frequency",
    "weight": 0.15,
    "coins_traded": ["BTC", "ETH", "SOL", "..."],
    "coin_frequency": {"BTC": 0.35, "ETH": 0.25, "SOL": 0.15},
    "never_traded": ["DOGE", "SHIB"],
    "top_5_by_volume": ["BTC", "ETH", "SOL", "ARB", "AVAX"]
  },

  "leverage_profile": {
    "description": "Leverage habits per asset — very hard to change",
    "weight": 0.15,
    "per_coin": {
      "BTC": {"mean": 10.2, "median": 10, "mode": 10, "std": 2.1, "type": "cross"},
      "ETH": {"mean": 7.5, "median": 7, "mode": 5, "std": 3.0, "type": "cross"}
    },
    "overall": {"mean": 8.5, "median": 8, "max_ever": 50}
  },

  "position_sizing": {
    "description": "How large positions are relative to account — subconscious habit",
    "weight": 0.12,
    "size_to_account_ratio": {"mean": 0.15, "median": 0.12, "std": 0.08},
    "notional_ranges": {
      "BTC": {"typical_min_usd": 50000, "typical_max_usd": 500000},
      "ETH": {"typical_min_usd": 30000, "typical_max_usd": 300000}
    },
    "scaling_behavior": {
      "scales_in": true,
      "typical_tranches": 3,
      "tranche_size_pattern": "decreasing"
    }
  },

  "timing_profile": {
    "description": "When they trade — timezone fingerprint, nearly impossible to fake",
    "weight": 0.15,
    "hourly_distribution": [0.01, 0.01, 0.005, "...24 values..."],
    "day_of_week_distribution": [0.18, 0.16, 0.15, 0.14, 0.15, 0.12, 0.10],
    "most_active_hours_utc": [14, 15, 16, 17, 18, 19, 20],
    "least_active_hours_utc": [2, 3, 4, 5, 6],
    "inferred_timezone_offset": -5
  },

  "hold_duration": {
    "description": "How long positions are held — deeply habitual",
    "weight": 0.10,
    "overall_minutes": {"mean": 4320, "median": 2880, "p25": 720, "p75": 10080},
    "per_coin": {
      "BTC": {"mean_minutes": 5760, "median_minutes": 4320},
      "ETH": {"mean_minutes": 2880, "median_minutes": 1440}
    },
    "distribution_buckets": {
      "under_1h": 0.05,
      "1h_to_4h": 0.15,
      "4h_to_24h": 0.30,
      "1d_to_7d": 0.35,
      "over_7d": 0.15
    }
  },

  "entry_exit_style": {
    "description": "Market vs limit, distance from mark, cancel patterns",
    "weight": 0.10,
    "order_type_ratio": {"market": 0.40, "limit": 0.55, "stop": 0.05},
    "limit_distance_from_mark_bps": {"mean": 15, "median": 10, "std": 12},
    "cancel_rate": 0.25,
    "take_profit_style": "gradual_scale_out",
    "stop_loss_usage": 0.30,
    "typical_tp_pct": {"mean": 5.2, "median": 3.5},
    "typical_sl_pct": {"mean": 2.8, "median": 2.0}
  },

  "risk_management": {
    "description": "Drawdown behavior, margin utilization, funding sensitivity",
    "weight": 0.08,
    "max_drawdown_tolerance_pct": 15,
    "margin_utilization": {"typical": 0.35, "max_observed": 0.85},
    "holds_through_funding": true,
    "funding_rate_threshold_to_close": 0.01,
    "liquidation_count": 0,
    "max_simultaneous_positions": 5
  },

  "trade_sequencing": {
    "description": "Patterns in how trades are ordered and correlated",
    "weight": 0.08,
    "hedging_frequency": 0.15,
    "correlated_pairs": [["BTC", "ETH"], ["SOL", "AVAX"]],
    "typical_open_sequence": "largest_first",
    "typical_close_sequence": "winners_first",
    "multi_asset_entry_delay_minutes": 15
  },

  "account_characteristics": {
    "description": "Account size and volume bracket",
    "weight": 0.07,
    "account_value_range_usd": [500000, 2000000],
    "weekly_volume_usd": {"mean": 5000000, "median": 3500000},
    "fee_tier": "VIP2",
    "cumulative_volume": "2854574.59"
  }
}
```

### 7.2 Similarity Scoring

For each candidate wallet, compute a weighted cosine similarity across all fingerprint dimensions:

```
similarity = sum(weight_i * dimension_similarity_i) / sum(weight_i)
```

Each dimension uses an appropriate distance metric:
- **Asset preferences:** Jaccard similarity on coin sets + correlation on frequency vectors
- **Leverage profile:** Per-coin mean/std comparison using Gaussian overlap
- **Timing profile:** Cosine similarity on hourly distribution vectors
- **Position sizing:** Overlap of notional ranges + ratio distribution comparison
- **Hold duration:** Distribution bucket comparison (chi-squared or KL divergence)
- **Entry/exit style:** Euclidean distance on normalized feature vector

**Alert thresholds:**
- `>= 0.85` similarity: HIGH confidence alert (immediate email)
- `>= 0.70` similarity: MEDIUM confidence alert (included in daily report)
- `>= 0.55` similarity: LOW confidence (logged for review)

---

## 8. Backfill Strategy

**CRITICAL:** Must be run IMMEDIATELY on first deployment to capture all available historical data before it expires.

### 8.1 Backfill Process

1. **Fills:** Use `userFillsByTime` with `startTime=0` (epoch), paginate in 24-hour windows. Keep requesting until no results. Store every fill with deduplication by fill hash.

2. **Funding:** Use `userFunding` with `startTime=0`, paginate in 7-day windows. Store all funding payments.

3. **Ledger:** Use `userNonFundingLedgerUpdates` with `startTime=0`, paginate in 7-day windows. Captures all deposits, withdrawals, transfers, and liquidations.

4. **Orders:** `historicalOrders` returns last 2000. Capture immediately.

5. **L1 Transactions:** Use Etherscan V2 API to pull ALL transactions and token transfers for the wallet on Arbitrum. Paginate using page/offset.

6. **Current State:** Snapshot `clearinghouseState`, `openOrders`, `frontendOpenOrders`, `subAccounts`, `userVaultEquities`, `portfolio`, `userFees`, `referral`, `userRateLimit`.

### 8.2 Deduplication

Every record is deduplicated using a unique key:
- Fills: `hash` field (transaction hash)
- Funding: `hash` + `time`
- Ledger: `hash` + `time`
- Orders: `oid` (order ID)
- L1 transactions: `hash` (transaction hash)

### 8.3 Cursor Management

After backfill and after each collection cycle, update cursor files in `data/state/`:
- `last_fill_time.txt`: Timestamp of most recent fill
- `last_funding_time.txt`: Timestamp of most recent funding payment
- `last_ledger_time.txt`: Timestamp of most recent ledger entry
- `last_l1_block.txt`: Last scanned Arbitrum block number

The next collection cycle uses these cursors as `startTime` to only fetch new data.

---

## 9. Email Alert System

### 9.1 Provider

**Brevo (formerly Sendinblue):** Free tier — 300 emails/day, SMTP access.
- SMTP server: `smtp-relay.brevo.com`
- Port: 587 (TLS)
- Auth: API key as password

### 9.2 Alert Types

| Alert | Trigger | Priority |
|-------|---------|----------|
| Fund Movement | Any withdrawal/USDC transfer OUT detected on HL or L1 | CRITICAL |
| New Wallet Found (Direct) | Funds traced from target wallet to new HL deposit | CRITICAL |
| New Wallet Found (Behavioral) | Wallet scores >= 0.85 similarity | HIGH |
| Daily Summary | Cron at 00:00 UTC | NORMAL |

### 9.3 Email Content

**Fund Movement Alert:**
```
Subject: [EZEKIEL] CRITICAL: Fund Movement Detected

Wallet: 0x45d26f28196d226497130c4bac709d808fed4029
Event: Withdrawal of $XXX,XXX USDC
Destination: 0x[destination_address]
Time: 2026-XX-XX XX:XX UTC
Tracing Status: [In progress / Complete]

If destination deposited to Hyperliquid:
  NEW WALLET DETECTED: 0x[new_address]
  Confidence: DIRECT FUND TRACE (100%)
```

**Behavioral Match Alert:**
```
Subject: [EZEKIEL] HIGH: Potential Ezekiel Wallet Detected (87% match)

Candidate Wallet: 0x[address]
Similarity Score: 0.87 / 1.00

Matching Dimensions:
  - Timing profile: 0.92 (trades same hours)
  - Leverage profile: 0.89 (same leverage per coin)
  - Asset preferences: 0.88 (same coin selection)
  - Position sizing: 0.85 (same size ratios)
  - Hold duration: 0.82 (similar hold times)

Recent Activity:
  - Opened 10x long BTC at $XX,XXX
  - Account size: ~$X,XXX,XXX
  - Active since: 2026-XX-XX
```

---

## 10. Configuration

```json
{
  "target_wallet": "0x45d26f28196d226497130c4bac709d808fed4029",
  "hyperliquid_api": "https://api.hyperliquid.xyz/info",
  "leaderboard_url": "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
  "etherscan_v2_base": "https://api.etherscan.io/v2/api",
  "arbitrum_chain_id": 42161,
  "hl_bridge_contract": "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7",
  "usdc_contract_arbitrum": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
  "alert_thresholds": {
    "similarity_high": 0.85,
    "similarity_medium": 0.70,
    "similarity_low": 0.55
  },
  "collection_intervals": {
    "positions_and_fills_minutes": 5,
    "funding_and_ledger_minutes": 15,
    "subaccounts_and_vaults_minutes": 60,
    "l1_trace_minutes": 15,
    "scanner_minutes": 60,
    "fingerprint_rebuild_hours": 24
  },
  "email": {
    "smtp_host": "smtp-relay.brevo.com",
    "smtp_port": 587,
    "from": "ezekiel@alerts.dev",
    "to": "<USER_EMAIL>"
  },
  "scanner": {
    "max_leaderboard_wallets": 500,
    "fills_lookback_days": 7,
    "min_fills_for_comparison": 20
  }
}
```

---

## 11. GitHub Actions Workflows

### 11.1 collect.yml — Every 5 Minutes

```yaml
name: Collect Trading Data
on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/collector.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: collect trading data [automated]"
          file_pattern: "data/**"
```

### 11.2 backfill.yml — Manual Trigger (One-Time)

```yaml
name: Historical Backfill
on:
  workflow_dispatch:

jobs:
  backfill:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/backfill.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: historical backfill [automated]"
          file_pattern: "data/**"
```

### 11.3 trace.yml — Every 15 Minutes

```yaml
name: Trace Fund Flows
on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:

jobs:
  trace:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/tracer.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: trace fund flows [automated]"
          file_pattern: "data/**"
    env:
      ETHERSCAN_API_KEY: ${{ secrets.ETHERSCAN_API_KEY }}
      BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
      ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
```

### 11.4 scan.yml — Every Hour

```yaml
name: Scan for Matching Wallets
on:
  schedule:
    - cron: '0 * * * *'
  workflow_dispatch:

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/scanner.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "data: scanner results [automated]"
          file_pattern: "data/scans/** profile/**"
    env:
      BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
      ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
```

### 11.5 analyze.yml — Daily

```yaml
name: Daily Analysis & Fingerprint Rebuild
on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:

jobs:
  analyze:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python src/fingerprint.py
      - uses: stefanzweifel/git-auto-commit-action@v5
        with:
          commit_message: "analysis: rebuild fingerprint + daily report [automated]"
          file_pattern: "profile/** reports/**"
    env:
      BREVO_SMTP_KEY: ${{ secrets.BREVO_SMTP_KEY }}
      ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
```

---

## 12. GitHub Secrets Required

| Secret | Value | Where to Get |
|--------|-------|--------------|
| `ETHERSCAN_API_KEY` | Free API key for Etherscan V2 | https://etherscan.io/myapikey |
| `BREVO_SMTP_KEY` | Brevo SMTP API key | https://app.brevo.com/ (Settings > SMTP & API) |
| `ALERT_EMAIL` | Your email address | Your email |

---

## 13. Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12 |
| HTTP Client | `requests` |
| Data Storage | JSON files (append-only per day, committed to git) |
| Fingerprint Math | `numpy`, `scipy` (similarity metrics) |
| Document Parsing | `python-docx` (Word), `PyPDF2` (PDF) |
| Email | `smtplib` (stdlib) + Brevo SMTP |
| Scheduling | GitHub Actions cron |
| Git Automation | `stefanzweifel/git-auto-commit-action@v5` |

---

## 14. Data Integrity Guarantees

1. **Append-only storage:** Data files are never overwritten. New records are appended to daily JSON files.
2. **Deduplication:** Every record is checked against existing data using unique keys before appending.
3. **Cursor-based collection:** State files track the last collected timestamp for each data type. If a collection run is missed, the next run picks up from where we left off.
4. **Git history:** Every data commit is preserved in git history, providing a full audit trail.
5. **Backfill on deploy:** The first action is a complete historical pull to capture all available data before it expires from the API.

---

## 15. Risk Mitigations

| Risk | Mitigation |
|------|------------|
| GitHub Actions cron delays (can be up to 15 min late) | Use `userFillsByTime` with cursor — we never miss data, just detect it later |
| API rate limits (1200 req/min) | Batch requests efficiently; 14 endpoints * 12/hour = 168 req/hour (well under limit) |
| Etherscan free tier (5 req/sec) | Add 0.25s delay between L1 API calls |
| Git repo size growth | JSON is compact; estimate ~1MB/day for single trader = ~365MB/year (within GitHub limits) |
| GitHub Actions limits on public repos | Monitor usage; workflows are lightweight (< 2 min each) |
| Leaderboard data not available via official API | Scrape from stats endpoint; fall back to checking top wallets individually |

---

## 16. Twitter / Social Media Intelligence Layer

### 16.1 Design Principle

The wallet and Twitter accounts are tracked as **separate, independent entities**. The GCR hypothesis is unconfirmed. The system:
- Collects Twitter data independently of wallet data
- Runs **correlation analysis** to test whether the accounts are linked
- Never assumes linkage — only reports statistical evidence

### 16.2 Twitter Accounts Monitored

| Account | Role | Status |
|---------|------|--------|
| `@GiganticRebirth` | Primary account of interest | Rarely active |
| `@GCRClassic` | Secondary account of interest | Rarely active |

### 16.3 Data Collection Method

**X/Twitter API free tier (2026) is write-only — read access requires $100/mo Basic tier.** Free alternatives:

| Method | Implementation | Schedule |
|--------|---------------|----------|
| **RSS Bridge** | Use `rss.app` or `nitter.net` RSS feeds to monitor new tweets | Every hour via GitHub Actions |
| **Historical Archive** | User-provided research docs (`research/GCR.docx`, `research/Trade Reviews.pdf`) | Parsed once, updated manually |
| **Wayback Machine API** | `https://web.archive.org/web/timemap/json?url=twitter.com/GiganticRebirth` | One-time backfill |

### 16.4 Data Captured Per Tweet

```json
{
  "source_account": "@GiganticRebirth",
  "tweet_id": "1234567890",
  "timestamp": "2026-02-19T14:32:00Z",
  "text": "Full tweet text",
  "has_trading_content": true,
  "mentioned_coins": ["BTC", "ETH"],
  "sentiment": "bullish",
  "mentioned_direction": "long",
  "mentioned_leverage": null,
  "mentioned_target": "$120K"
}
```

### 16.5 Correlation Analysis

The system computes a **tweet-to-trade correlation score** by comparing:

1. **Timing correlation:** When a tweet mentions a coin, did the Ezekiel wallet open/close a position on that coin within a time window (e.g., 1h before to 4h after)?
2. **Direction correlation:** If a tweet says "bullish BTC", did the wallet go long BTC?
3. **Volume correlation:** Does trading volume spike around tweet times?

If correlations are consistently strong, this provides evidence that the wallet and Twitter accounts belong to the same person. If correlations are weak or random, they likely don't.

**Output:** `profile/twitter_correlation.json`

```json
{
  "hypothesis": "Wallet owner is @GiganticRebirth / @GCRClassic",
  "confidence": "LOW | MEDIUM | HIGH",
  "evidence": {
    "timing_correlation": 0.72,
    "direction_correlation": 0.85,
    "sample_size": 45,
    "time_range": "2024-06-15 to 2026-02-19"
  },
  "notable_matches": [
    {
      "tweet": "@GiganticRebirth tweeted 'BTC looks ready' at 14:32 UTC",
      "trade": "Wallet opened 10x long BTC at 14:47 UTC (15 min later)",
      "correlation_type": "timing + direction"
    }
  ]
}
```

### 16.6 How This Helps Find Future Wallets

Even if the trader switches wallets, if they continue tweeting (from any account):
- We can compare new tweet timestamps against trading activity on candidate wallets
- A new wallet that consistently trades 10-30 min after tweets from a specific account is a strong signal
- This works as an **independent verification layer** alongside the behavioral fingerprint

### 16.7 Storage

```
data/
├── twitter/
│   ├── tweets/                    # Raw tweet data by date
│   │   └── YYYY-MM-DD.json
│   ├── archive/                   # Historical tweet backfill
│   │   ├── giganticrebirth.json
│   │   └── gcrclassic.json
│   └── correlation/               # Correlation analysis results
│       └── latest.json
```

### 16.8 Workflow Addition

```yaml
# Added to analyze.yml (daily) or as separate workflow
- name: Fetch new tweets
  run: python src/twitter_monitor.py

- name: Run correlation analysis
  run: python src/twitter_correlator.py
```

### 16.9 New Python Modules

| Module | Purpose |
|--------|---------|
| `src/twitter_monitor.py` | Fetches new tweets via RSS bridge, parses content, extracts trading signals |
| `src/twitter_correlator.py` | Correlates tweet timestamps/content against wallet fills, computes confidence score |

---

## 17. Additional Tracking Ideas (Future Enhancements)

1. **Cross-DEX monitoring:** Check if the wallet or fund-traced wallets appear on other perp DEXes (dYdX, GMX, Vertex). Same trader often trades across platforms.

2. **Cross-DEX monitoring:** Check if the wallet or fund-traced wallets appear on other perp DEXes (dYdX, GMX, Vertex). Same trader often trades across platforms.

3. **Arkham Intelligence integration:** If Arkham labels this wallet or linked wallets, it can provide entity information. Free tier provides basic labels.

4. **HyperTracker cohort analysis:** CoinMarketMan's HyperTracker groups wallets by PnL and size cohorts. Monitor which cohort GCR falls into, then watch for new wallets entering that exact cohort.

5. **Transaction timing microstructure:** Analyze the exact millisecond-level timestamps of transactions. Infrastructure-dependent patterns (bot latency, manual click patterns) are unique.

6. **Referral chain tracking:** If the wallet uses a referral code, other wallets using the same code may be related. Monitor the `referral` endpoint.

7. **Subaccount monitoring:** The `subAccounts` endpoint directly reveals linked wallets. If subaccounts appear, they are confirmed related addresses.

8. **Deposit source analysis:** When the wallet receives deposits, trace where the USDC came from. The funding source address pattern is an identity signal.

---

## 17. Success Criteria

- [ ] All available historical data captured within 24 hours of deployment
- [ ] Zero data gaps in ongoing collection (no missed fills, funding, or ledger entries)
- [ ] Behavioral fingerprint generated with all dimensions populated
- [ ] Fund flow tracing operational and alerting on any withdrawals
- [ ] Scanner running hourly against leaderboard wallets
- [ ] Email alerts delivered within 5 minutes of trigger events
- [ ] If the trader moves wallets, the system detects the new wallet within 24 hours (via fund trace) or 7 days (via behavioral matching)
