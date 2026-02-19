# Ezekiel: System Architecture

> Technical architecture for the Ezekiel trader intelligence and fingerprinting system.
> Companion to [PRD.md](../../PRD.md).

---

## 1. System Overview

Ezekiel is a three-tier system:

1. **Backend** (Python) — Data collection, fingerprinting, scanning, tracing, alerts. Runs headless on GitHub Actions 24/7.
2. **Dashboard** (SvelteKit + Vite) — Visual interface for exploring collected data, the behavioral fingerprint, scanner results, and fund flows. Static site deployed to GitHub Pages.
3. **Data Layer** (JSON files in Git) — Append-only JSON files organized by type and date. Serves as both persistent storage and the API for the dashboard (via raw GitHub URLs).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          GitHub Repository                              │
│                                                                         │
│  ┌──────────────────────┐     ┌──────────────────────────────────────┐ │
│  │   Backend (Python)    │     │       Dashboard (SvelteKit)          │ │
│  │   GitHub Actions      │     │       GitHub Pages                   │ │
│  │                        │     │                                      │ │
│  │  ┌────────────────┐   │     │  ┌────────┐ ┌──────────┐ ┌───────┐ │ │
│  │  │  collector.py   │   │     │  │Positions│ │Fingerprint│ │Scanner│ │ │
│  │  │  backfill.py    │   │     │  │ View    │ │  Radar   │ │Results│ │ │
│  │  │  tracer.py      │   │     │  └────────┘ └──────────┘ └───────┘ │ │
│  │  │  scanner.py     │   │     │  ┌────────┐ ┌──────────┐ ┌───────┐ │ │
│  │  │  fingerprint.py │   │     │  │ Fills  │ │Fund Flow │ │Reports│ │ │
│  │  │  alerts.py      │   │     │  │Timeline│ │  Tracer  │ │ Daily │ │ │
│  │  └───────┬─────────┘   │     │  └────────┘ └──────────┘ └───────┘ │ │
│  │          │              │     │         │                           │ │
│  └──────────┼──────────────┘     └─────────┼───────────────────────────┘ │
│             │ writes                       │ reads (raw.githubusercontent)│
│             ▼                              ▼                             │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                       data/ (JSON files in Git)                     │ │
│  │  positions/ fills/ orders/ funding/ ledger/ account/ scans/ ...     │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│  ┌─────────────────────┐                                                │
│  │  profile/            │                                                │
│  │  fingerprint.json    │                                                │
│  │  trader_profile.json │                                                │
│  └─────────────────────┘                                                │
│                                                                         │
│  ┌─────────────────────┐                                                │
│  │  reports/daily/      │                                                │
│  └─────────────────────┘                                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Updated Project Structure

```
Ezekiel/
├── .github/
│   └── workflows/
│       ├── collect.yml              # Cron: every 5 min
│       ├── backfill.yml             # Manual trigger
│       ├── scan.yml                 # Cron: every 1 hour
│       ├── analyze.yml              # Cron: daily
│       ├── trace.yml                # Cron: every 15 min
│       └── deploy-dashboard.yml     # On push to main (dashboard changes)
│
├── src/                             # Backend (Python)
│   ├── collector.py
│   ├── backfill.py
│   ├── fingerprint.py
│   ├── scanner.py
│   ├── tracer.py
│   ├── alerts.py
│   ├── profile_builder.py
│   └── utils.py
│
├── dashboard/                       # Frontend (SvelteKit + Vite)
│   ├── package.json
│   ├── svelte.config.js
│   ├── vite.config.js
│   ├── src/
│   │   ├── app.html
│   │   ├── app.css                  # Global styles + dark theme
│   │   ├── lib/
│   │   │   ├── api.js               # Data fetching from raw GitHub URLs
│   │   │   ├── stores.js            # Svelte stores for shared state
│   │   │   ├── utils.js             # Formatting, date helpers
│   │   │   └── components/
│   │   │       ├── PositionCard.svelte
│   │   │       ├── FillsTable.svelte
│   │   │       ├── FingerprintRadar.svelte
│   │   │       ├── TimelineChart.svelte
│   │   │       ├── ScannerResults.svelte
│   │   │       ├── FundFlowGraph.svelte
│   │   │       ├── AccountSummary.svelte
│   │   │       └── AlertBanner.svelte
│   │   └── routes/
│   │       ├── +layout.svelte       # Shell: sidebar nav + dark theme
│   │       ├── +page.svelte         # Dashboard home: live positions + account
│   │       ├── fills/
│   │       │   └── +page.svelte     # Trade history with filters
│   │       ├── fingerprint/
│   │       │   └── +page.svelte     # Behavioral fingerprint visualization
│   │       ├── scanner/
│   │       │   └── +page.svelte     # Wallet match results + scores
│   │       ├── fund-flow/
│   │       │   └── +page.svelte     # L1 transaction tracing
│   │       ├── twitter/
│   │       │   └── +page.svelte     # Twitter correlation analysis
│   │       └── reports/
│   │           └── +page.svelte     # Daily report viewer
│   └── static/
│       └── .nojekyll                # Required for GitHub Pages
│
├── data/                            # Collected data (JSON, git-tracked)
│   ├── positions/
│   ├── fills/
│   ├── orders/
│   ├── funding/
│   ├── ledger/
│   ├── account/
│   ├── subaccounts/
│   ├── vaults/
│   ├── fees/
│   ├── referral/
│   ├── rate_limit/
│   ├── l1_transactions/
│   ├── scans/
│   ├── twitter/
│   │   ├── tweets/                  # New tweets by date
│   │   ├── archive/                 # Historical tweet backfill per account
│   │   └── correlation/             # Tweet-to-trade correlation results
│   └── state/
│
├── research/                        # User's GCR research docs
├── profile/                         # Computed fingerprint + profile
├── reports/                         # Daily summary reports
├── config.json
├── requirements.txt                 # Python deps
├── PRD.md
└── docs/
    └── architecture.md              # This document
```

---

## 3. Backend Architecture (Python)

### 3.1 Module Responsibilities

```
src/
├── collector.py            ─── Polls 14 HL endpoints, appends to data/
├── backfill.py             ─── One-time historical pull with pagination
├── fingerprint.py          ─── Reads ALL data/, computes profile/fingerprint.json
├── scanner.py              ─── Pulls leaderboard, scores each wallet vs fingerprint
├── tracer.py               ─── Monitors Arbitrum L1 for fund movements
├── alerts.py               ─── Sends email via Brevo SMTP
├── profile_builder.py      ─── Parses research/*.docx + *.pdf into profile
├── twitter_monitor.py      ─── Fetches tweets via RSS bridge, extracts trading signals
├── twitter_correlator.py   ─── Correlates tweet timing/content vs wallet trades
└── utils.py                ─── Shared: API calls, file I/O, dedup, cursors
```

### 3.3 Key Design Principle: Separate Entities

The wallet and Twitter accounts are tracked as **independent entities**. The system does NOT assume they belong to the same person. Instead:

- **Wallet fingerprint** = built purely from on-chain Hyperliquid data
- **Twitter profile** = built independently from tweet content and timing
- **Correlation analysis** = statistical test of whether the two are linked
- The GCR hypothesis is treated as testable, not assumed

### 3.2 utils.py — Core Abstractions

```python
# Key functions provided by utils.py:

def hl_post(request_body: dict) -> dict | list:
    """POST to https://api.hyperliquid.xyz/info, return JSON response."""

def etherscan_get(params: dict) -> dict:
    """GET Etherscan V2 API with chainid=42161, rate-limit 0.25s between calls."""

def read_cursor(name: str) -> int:
    """Read timestamp cursor from data/state/{name}.txt, return 0 if missing."""

def write_cursor(name: str, value: int) -> None:
    """Write timestamp cursor to data/state/{name}.txt."""

def append_records(directory: str, records: list, key_field: str) -> int:
    """Append records to today's JSON file, deduplicate by key_field. Returns count added."""

def load_all_records(directory: str) -> list:
    """Load and merge ALL JSON files in a data directory across all dates."""
```

### 3.3 Data Flow Per Module

**collector.py** (every 5 min):
```
1. Read cursors (last_fill_time, last_funding_time, last_ledger_time)
2. Poll HL endpoints using cursors as startTime
3. Append new records to data/{type}/YYYY-MM-DD.json
4. Update cursors with latest timestamps
5. Snapshot positions + account state to data/positions/YYYY-MM-DD/HH-MM.json
```

**tracer.py** (every 15 min):
```
1. Read last_l1_block cursor
2. Query Etherscan V2: tokentx for USDC transfers
3. Compare against known transactions (dedup by hash)
4. If new OUTBOUND transfer detected:
   a. Record destination address
   b. Query destination's tokentx — did they deposit to HL bridge?
   c. If yes → CRITICAL alert (new wallet found)
   d. If no → follow 1 more hop, log for review
5. Update last_l1_block cursor
```

**scanner.py** (every hour):
```
1. Load profile/fingerprint.json
2. Fetch leaderboard from stats-data.hyperliquid.xyz
3. For each candidate wallet (up to 500):
   a. Query clearinghouseState (positions, leverage, account size)
   b. Query recent userFillsByTime (last 7 days)
   c. Compute fingerprint similarity score
4. Rank by similarity, save to data/scans/YYYY-MM-DD.json
5. If any score >= 0.85 → HIGH alert
6. If any score >= 0.70 → include in daily report
```

**fingerprint.py** (daily):
```
1. Load ALL data from data/fills/, data/positions/, data/orders/, etc.
2. Compute each fingerprint dimension (see PRD Section 7)
3. Write profile/fingerprint.json
4. Generate reports/daily/YYYY-MM-DD.md
5. Send daily summary email
```

### 3.4 Python Dependencies

```
requests>=2.31.0
numpy>=1.26.0
scipy>=1.12.0
python-docx>=1.1.0
PyPDF2>=3.0.0
```

---

## 4. Frontend Architecture (SvelteKit + Vite)

### 4.1 Tech Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Framework | SvelteKit | 2.x (Svelte 5) |
| Build Tool | Vite | 6.x |
| Adapter | @sveltejs/adapter-static | Latest |
| Charts | Chart.js + svelte-chartjs | 4.x |
| Deployment | GitHub Pages | Free |
| Styling | CSS custom properties (no Tailwind) | - |

### 4.2 Why SvelteKit + Vite (Not Plain Svelte)

- SvelteKit provides file-based routing (clean URLs: `/fills`, `/fingerprint`, `/scanner`)
- `adapter-static` outputs a fully static site — perfect for GitHub Pages
- Vite is the default build tool for SvelteKit
- Built-in layout system for the dashboard shell

### 4.3 Key Configuration

**svelte.config.js:**
```javascript
import adapter from '@sveltejs/adapter-static';

const config = {
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: '404.html',
      precompress: false,
      strict: false
    }),
    paths: {
      base: process.argv.includes('dev') ? '' : '/Ezekiel'
    }
  }
};

export default config;
```

**vite.config.js:**
```javascript
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()]
});
```

**src/routes/+layout.js:**
```javascript
export const prerender = true;
export const ssr = false;  // Pure client-side SPA — data fetched at runtime
```

### 4.4 Data Fetching Strategy

The dashboard is a **static SPA** that fetches JSON data at runtime from the same GitHub repo using raw URLs:

```javascript
// dashboard/src/lib/api.js

const REPO = 'YOUR_USERNAME/Ezekiel';
const BRANCH = 'main';
const BASE = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;

export async function fetchJSON(path) {
  const res = await fetch(`${BASE}/${path}`);
  if (!res.ok) return null;
  return res.json();
}

// Examples:
// fetchJSON('profile/fingerprint.json')
// fetchJSON('data/positions/2026-02-19/14-30.json')
// fetchJSON('data/fills/2026-02-19.json')
// fetchJSON('data/scans/2026-02-19.json')
// fetchJSON('reports/daily/2026-02-19.md')
```

**Why this works:**
- `raw.githubusercontent.com` serves raw file content from the repo
- GitHub Actions commits new data every 5 min → dashboard shows near-real-time data
- No backend server needed — the dashboard is purely static
- Free, no CORS issues (raw.githubusercontent.com allows cross-origin)

**Data freshness:** The dashboard polls for updates every 60 seconds (configurable). Since GitHub Actions commits data every 5 min, the dashboard is at most ~6 min behind real-time.

### 4.5 Dashboard Pages

#### Home (`/`) — Live Overview
```
┌─────────────────────────────────────────────────────────────┐
│  EZEKIEL — Trader Intelligence Dashboard                     │
├─────────┬───────────────────────────────────────────────────┤
│         │                                                    │
│  NAV    │  ┌─────────────────┐  ┌─────────────────────────┐│
│         │  │ Account Summary  │  │  Current Positions       ││
│  Home   │  │ Value: $1.2M     │  │  BTC  10x Long  +$45K   ││
│  Fills  │  │ PnL: +$89K       │  │  ETH   5x Short -$12K   ││
│  Finger │  │ Margin: 35%      │  │  SOL  20x Long  +$8K    ││
│  Scanner│  └─────────────────┘  └─────────────────────────┘│
│  Flows  │                                                    │
│  Reports│  ┌─────────────────────────────────────────────┐  │
│         │  │  Account Value Over Time (Line Chart)        │  │
│         │  │  ████████████████████████████████████████    │  │
│         │  └─────────────────────────────────────────────┘  │
│         │                                                    │
│         │  ┌─────────────────────────────────────────────┐  │
│         │  │  Recent Fills (Last 24h)                     │  │
│         │  │  14:32  BTC  Long   0.5  $98,500  +$1,200   │  │
│         │  │  14:15  ETH  Short  10   $3,450   -$340     │  │
│         │  └─────────────────────────────────────────────┘  │
└─────────┴───────────────────────────────────────────────────┘
```

#### Fills (`/fills`) — Complete Trade History
- Sortable/filterable table of ALL recorded fills
- Filter by: coin, side (long/short), date range, PnL (+/-)
- Columns: Time, Coin, Side, Size, Price, PnL, Fee, Direction
- Export to CSV button

#### Fingerprint (`/fingerprint`) — Behavioral Profile
- **Radar chart** showing all 10 fingerprint dimensions
- Per-dimension detail cards with distributions:
  - Timing heatmap (hour x day-of-week)
  - Leverage distribution histogram
  - Asset frequency pie chart
  - Hold duration distribution
  - Position sizing scatter plot
- Computed similarity weights displayed

#### Scanner (`/scanner`) — Wallet Match Results
- Table of candidate wallets ranked by similarity score
- Color-coded: red (>0.85), yellow (>0.70), grey (<0.70)
- Click a wallet to expand: shows which dimensions match
- Side-by-side comparison: Ezekiel fingerprint vs candidate

#### Fund Flow (`/fund-flow`) — L1 Transaction Tracing
- Timeline of all deposits/withdrawals
- Visual graph: source → wallet → destination → (next hop)
- Highlight any addresses that deposited to Hyperliquid bridge
- Status badges: TRACED / PENDING / NO HL DEPOSIT

#### Twitter Intel (`/twitter`) — Social Media Correlation
- Tweet timeline for @GiganticRebirth and @GCRClassic
- Overlay: tweet timestamps vs trade timestamps on same chart
- Correlation score dashboard: timing, direction, confidence level
- **Hypothesis status badge**: LOW / MEDIUM / HIGH confidence that wallet = GCR
- Individual correlation matches (tweet X → trade Y, N minutes apart)

#### Reports (`/reports`) — Daily Summaries
- List of daily reports by date
- Rendered markdown with key metrics:
  - Trades taken, PnL, new positions, closed positions
  - Scanner highlights, fund flow events
  - Fingerprint changes (if any dimensions shifted)
  - Twitter correlation updates

### 4.6 Svelte 5 Patterns Used

**Runes (Svelte 5 reactivity):**
```svelte
<script>
  // Reactive state
  let positions = $state([]);
  let selectedCoin = $state('ALL');

  // Derived values
  let filteredPositions = $derived(
    selectedCoin === 'ALL'
      ? positions
      : positions.filter(p => p.position.coin === selectedCoin)
  );

  // Side effects (data fetching)
  $effect(() => {
    const interval = setInterval(async () => {
      positions = await fetchJSON('data/positions/latest.json') ?? [];
    }, 60_000);
    return () => clearInterval(interval);
  });
</script>
```

**Event handlers (Svelte 5 syntax):**
```svelte
<!-- Svelte 5: onclick instead of on:click -->
<button onclick={() => selectedCoin = 'BTC'}>BTC</button>

<!-- Props via $props() instead of export let -->
<script>
  let { data, label } = $props();
</script>
```

**Component composition:**
```svelte
<!-- No createEventDispatcher — use callback props -->
<FillsTable
  fills={filteredFills}
  onRowClick={(fill) => selectedFill = fill}
/>
```

### 4.7 Chart.js Integration

```svelte
<!-- Example: Account Value Line Chart -->
<script>
  import { Line } from 'svelte-chartjs';
  import {
    Chart, LineElement, PointElement, LinearScale,
    TimeScale, Tooltip, Legend, Filler
  } from 'chart.js';
  import 'chartjs-adapter-date-fns';

  Chart.register(LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend, Filler);

  let { data } = $props();

  let chartData = $derived({
    datasets: [{
      label: 'Account Value',
      data: data.map(([ts, val]) => ({ x: ts, y: parseFloat(val) })),
      borderColor: '#10b981',
      backgroundColor: 'rgba(16, 185, 129, 0.1)',
      fill: true,
      tension: 0.3
    }]
  });
</script>

<Line {data} options={{
  responsive: true,
  scales: {
    x: { type: 'time', grid: { color: '#1e293b' } },
    y: { grid: { color: '#1e293b' }, ticks: { color: '#94a3b8' } }
  },
  plugins: { legend: { labels: { color: '#e2e8f0' } } }
}} />
```

### 4.8 Dark Theme (CSS Custom Properties)

```css
/* dashboard/src/app.css */

:root {
  --bg-primary: #0f172a;
  --bg-secondary: #1e293b;
  --bg-card: #1e293b;
  --bg-hover: #334155;
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --border: #334155;
  --accent-green: #10b981;
  --accent-red: #ef4444;
  --accent-blue: #3b82f6;
  --accent-yellow: #f59e0b;
  --accent-purple: #8b5cf6;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
  --font-sans: 'Inter', -apple-system, sans-serif;
}

body {
  background: var(--bg-primary);
  color: var(--text-primary);
  font-family: var(--font-sans);
  margin: 0;
}

/* Utility classes for the dashboard */
.card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem;
}

.profit { color: var(--accent-green); }
.loss { color: var(--accent-red); }
.mono { font-family: var(--font-mono); }
```

---

## 5. Data Architecture

### 5.1 JSON File Conventions

All data files follow these conventions:

- **Daily files:** `data/{type}/YYYY-MM-DD.json` — Array of records, append-only
- **Snapshot files:** `data/{type}/YYYY-MM-DD/HH-MM.json` — Full state snapshot
- **Singleton files:** `data/{type}/latest.json` — Most recent state, overwritten each cycle
- **Cursor files:** `data/state/{name}.txt` — Single integer (timestamp ms)

### 5.2 Data Index File

Each collection cycle generates `data/index.json` — a manifest of all available data files, used by the dashboard to discover what data exists:

```json
{
  "last_updated": "2026-02-19T14:35:00Z",
  "wallet": "0x45d26f28196d226497130c4bac709d808fed4029",
  "data_range": {
    "first_record": "2024-06-15T00:00:00Z",
    "last_record": "2026-02-19T14:30:00Z"
  },
  "files": {
    "positions": ["2026-02-18", "2026-02-19"],
    "fills": ["2024-06-15", "...", "2026-02-19"],
    "orders": ["2024-06-15", "...", "2026-02-19"],
    "funding": ["2024-06-15", "...", "2026-02-19"],
    "ledger": ["2024-06-15", "...", "2026-02-19"],
    "scans": ["2026-02-18", "2026-02-19"]
  },
  "latest": {
    "positions": "data/positions/2026-02-19/14-30.json",
    "account": "data/account/2026-02-19/14-30.json",
    "fingerprint": "profile/fingerprint.json"
  },
  "stats": {
    "total_fills": 12345,
    "total_funding_events": 8901,
    "total_ledger_events": 234,
    "total_position_snapshots": 56789
  }
}
```

### 5.3 Size Estimates

| Data Type | Records/Day | Size/Day | Size/Year |
|-----------|------------|----------|-----------|
| Position snapshots | 288 (every 5 min) | ~150 KB | ~55 MB |
| Fills | ~50-200 trades | ~30 KB | ~11 MB |
| Orders | ~100-500 | ~50 KB | ~18 MB |
| Funding | ~24-48 payments | ~5 KB | ~2 MB |
| Ledger | ~1-5 events | ~2 KB | ~1 MB |
| Account snapshots | 288 | ~100 KB | ~36 MB |
| L1 transactions | ~1-10 | ~5 KB | ~2 MB |
| Scanner results | 24 scans | ~50 KB | ~18 MB |
| **Total** | | **~400 KB** | **~143 MB** |

Well within GitHub's repo size limits (5 GB recommended max).

---

## 6. Infrastructure Architecture

### 6.1 GitHub Actions Workflow Schedule

```
Time ──────────────────────────────────────────────────────►

Every 5 min:    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
                collect.yml (positions, fills, orders, account)

Every 15 min:   ░   ░   ░   ░   ░   ░   ░   ░   ░   ░   ░
                trace.yml (L1 fund flows, funding, ledger)

Every 1 hour:   ░           ░           ░           ░
                scan.yml (leaderboard behavioral matching)

Daily (00:00):  ░
                analyze.yml (rebuild fingerprint, daily report)

On push:        ░ (when dashboard/ changes)
                deploy-dashboard.yml (build + deploy to GH Pages)
```

### 6.2 Dashboard Deployment Workflow

```yaml
# .github/workflows/deploy-dashboard.yml
name: Deploy Dashboard to GitHub Pages

on:
  push:
    branches: ['main']
    paths: ['dashboard/**']
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: 'pages'
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
      - name: Install dependencies
        working-directory: dashboard
        run: npm install
      - name: Build
        working-directory: dashboard
        run: npm run build
      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: dashboard/build

  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    needs: build
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

### 6.3 Secrets Configuration

| Secret | Purpose | Provider |
|--------|---------|----------|
| `ETHERSCAN_API_KEY` | Arbitrum L1 fund tracing | Free from etherscan.io |
| `BREVO_SMTP_KEY` | Email alerts | Free from brevo.com |
| `ALERT_EMAIL` | Recipient email address | Your email |

No secrets needed for the dashboard — it reads public repo data.

---

## 7. External APIs & Rate Limits

### 7.1 Hyperliquid API

| Attribute | Value |
|-----------|-------|
| Base URL | `https://api.hyperliquid.xyz/info` |
| Method | POST |
| Auth | None required |
| Rate Limit | 1200 weight/min/IP |
| Our Usage | ~14 endpoints x 12/hour = 168 requests/hour (14% of limit) |

### 7.2 Etherscan V2 API (Arbitrum)

| Attribute | Value |
|-----------|-------|
| Base URL | `https://api.etherscan.io/v2/api?chainid=42161` |
| Method | GET |
| Auth | API key (free) |
| Rate Limit | 5 calls/sec |
| Our Usage | 3 calls every 15 min = 0.003 calls/sec (0.07% of limit) |

### 7.3 Hyperliquid Leaderboard

| Attribute | Value |
|-----------|-------|
| URL | `https://stats-data.hyperliquid.xyz/Mainnet/leaderboard` |
| Method | GET |
| Auth | None |
| Rate Limit | Undocumented (use conservatively) |
| Our Usage | 1 call/hour |

### 7.4 raw.githubusercontent.com (Dashboard Data)

| Attribute | Value |
|-----------|-------|
| URL | `https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}` |
| Auth | None (public repo) |
| Rate Limit | 60 req/hour unauthenticated, 5000 req/hour with token |
| Our Usage | ~20-50 requests per dashboard page load |
| Cache | 5-minute CDN cache |

**Note:** GitHub's raw content has a ~5 minute CDN cache. This aligns well with our 5-minute collection cycle.

---

## 8. Security Considerations

| Concern | Mitigation |
|---------|------------|
| Public repo exposes wallet address | The target wallet is public blockchain data — no security risk |
| API keys in workflows | Stored in GitHub Secrets, never in code |
| Email address exposure | Stored in GitHub Secrets, never in code |
| Dashboard has no auth | Read-only, displays only public blockchain data |
| Research docs (GCR.docx, Trade Reviews.pdf) | In `research/` — acceptable since trader identity is public (GCR) |
| Git commit spam (every 5 min) | Automated commits only to `data/` — main code stays clean |

---

## 9. Scalability Path

The current architecture is designed for tracking **one wallet**. If expanded:

| Change | How to Scale |
|--------|-------------|
| Track multiple wallets | Add `wallets[]` array to config.json, loop in collector.py |
| More data storage | Move from git JSON to SQLite file (still in repo) or free Supabase |
| Real-time updates | Add WebSocket connection (requires a server, not free on GitHub Actions) |
| Machine learning fingerprint | Replace statistical similarity with sklearn/pytorch model |
| Cross-DEX tracking | Add modules for dYdX, GMX, Vertex APIs |

---

## 10. Decision Log

| Decision | Rationale | Alternatives Considered |
|----------|-----------|------------------------|
| GitHub Actions for compute | Free, reliable, cron support, no server management | Railway (not free), Render (free tier limited) |
| JSON files in git for storage | Simple, free, versioned, readable by dashboard via raw URLs | SQLite (harder for dashboard), Supabase (unnecessary complexity) |
| SvelteKit + Vite for dashboard | Fast, modern, file-based routing, adapter-static for GH Pages | React (heavier), plain HTML (no routing), Vue (less ergonomic) |
| Chart.js for visualizations | Mature, well-supported, svelte-chartjs wrapper exists | D3 (overkill), LayerCake (less charting primitives) |
| Brevo for email | 300/day free, SMTP access, reliable | Gmail SMTP (rate limits), SendGrid (100/day free) |
| Etherscan V2 for L1 tracing | Unified API across chains, free, well-documented | Arbiscan V1 (being deprecated), Alchemy (overkill) |
| Polling (not WebSocket) | GitHub Actions can't maintain persistent connections | WebSocket requires a server (not free) |
| CSS custom properties (no Tailwind) | Minimal deps, dark theme only, fast build | Tailwind (adds build complexity for simple dashboard) |
