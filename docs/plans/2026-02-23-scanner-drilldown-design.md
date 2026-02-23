# Scanner Drill-Down & Dashboard Enhancements

## Date: 2026-02-23

## Goal
Improve wallet identification capabilities by making scanner results actionable and the fingerprint signature visible.

## Priority Order
1. Scanner match drill-down (compare wallets side-by-side)
2. Scanner history tracking (score trends over time)
3. Fingerprint visualization (radar chart of dimensions)
4. Monitoring charts (equity curve, PnL, funding)

## Changes Made

### 1. Scanner — Fingerprint Data Saved (src/scanner.py)
- `_summarize_fingerprint()` extracts comparison-relevant data from each candidate
- Top 20 candidates get full fingerprint data saved alongside scores
- Remaining candidates only get dimension scores (keeps file size manageable)
- Data includes: coins traded, frequency, hourly activity, leverage, order type ratio, win rate, hold duration

### 2. Scanner Page — Drill-Down View (dashboard/src/routes/scanner/+page.svelte)
- Click any wallet row to expand a comparison panel
- Radar chart: 5-axis spider chart overlaying target vs candidate dimension scores
- Timing comparison: Side-by-side bar chart of hourly activity (24h UTC)
- Asset overlap: Shared coins highlighted in green, target-only in cyan, candidate-only in yellow
- Execution comparison: Market/limit ratio, win rate, leverage, hold duration side-by-side
- Score trend badges: Shows how many times a wallet has appeared across scans (up/down/stable/new)

### 3. Scanner History (dashboard/src/lib/api.js)
- `fetchScanHistory()` loads all daily scan files to track wallet appearances over time
- Wallets appearing consistently = higher confidence of being a linked wallet

### 4. Fingerprint Page (dashboard/src/routes/fingerprint/+page.svelte)
- Radar chart showing 9 dimension weights visually
- Day-of-week activity bar chart
- Key behavioral traits summary (leverage, order type, win rate, active hours, timezone)
- Cleaner layout with stat cards at top

### 5. Main Dashboard Charts (dashboard/src/routes/+page.svelte)
- Account Value chart: Now uses portfolio API (hourly data) instead of sparse account snapshots
- PnL chart: Uses portfolio pnlHistory for cumulative PnL tracking
- Cumulative Funding chart: Aggregates all funding events into running total
- Position Allocation doughnut: Shows notional breakdown by coin

### 6. OPSEC
- Renamed research/GCR.docx to research/source_analysis.docx
