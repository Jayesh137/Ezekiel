# Style-Aware Calibrated Matching — Design

**Date:** 2026-07-06
**Motivation:** The scanner produced a false positive: a wallet scored as a match despite a
completely different trading style. Root causes: (1) cosine similarity on smooth distributions
gives any two active traders a ~0.7 baseline; (2) the traits humans read as "style" (trade
frequency, long/short bias, scaling behavior, loss handling) are not measured; (3) mismatch
penalties are too weak to veto; (4) scores are uncalibrated against the population.

## 1. New style dimensions (`src/fingerprint.py`)

`compute_style_profile(fills)` — added to the full, recent, and candidate fingerprints:

- **activity**: position episodes/day (decision frequency — primary; raw fill counts are
  TWAP-inflated ~10x for the same human), fills/day, active-days ratio.
- **direction**: share of position-opens that are long vs short (from `dir`). Regime-
  dependent (the target flips all-long ↔ all-short), so low weight and never a veto.
- **position_management**: avg fills per position episode (scale-in/out habit), avg opens
  and closes per episode.
- **loss_handling**: median hold minutes for losing vs winning closes, avg win / avg loss
  magnitude ratio.
- **clip_sizes**: dispersion (std/mean) of per-fill notional (account-size invariant),
  round-size preference (share of fills with ≤2 significant digits in `sz`).
- `sufficient_data: false` when fills < 15 — comparisons then return `None` and the
  dimension is excluded with weights renormalized (thin data never fakes a signal).

## 2. Scoring changes (`src/scanner.py`)

- New comparison dimensions with weights (total rebalanced to 1.0): activity 0.11,
  direction_bias 0.03, position_management 0.06, loss_handling 0.03; existing dims reduced
  proportionally (asset_preferences 0.20, timing 0.14, leverage 0.10, hold_duration 0.09,
  entry_exit 0.07, account_size 0.06, trade_sequencing 0.06, position_sizing 0.05).
- `None` dimensions are dropped and remaining weights renormalized.
- **Hard vetoes** (applied only when both sides have `sufficient_data`); a veto caps the
  final score at 0.45 (below WEAK_LEAD), blocks behavioral alerts entirely, and records
  the reason in `evidence["vetoes"]`:
  - decision frequency (episodes/day) > 5× apart;
  - scalper/swing incompatibility: one side >70% of closed holds under 1h, other <15%.
  - (No direction/shorting veto: the backtest showed the target flipping all-long ↔
    all-short between adjacent windows — direction is market view, not identity.)
- Linkage/vault/xyz bonuses are applied **after** the cap check but a vetoed score stays
  capped at 0.60 max — hard on-chain evidence can still surface it, style mismatch keeps it
  below alert tier.

## 3. Population calibration (`src/calibration.py`)

- Every leaderboard sweep appends each scanned wallet's raw score to a rolling population
  file `data/calibration/population.json` (last 1000 samples).
- `score_percentile(score)` → percentile of the raw score vs population.
- Results carry `score_percentile`; HIGH behavioral alerts additionally require
  percentile ≥ 99 once the population has ≥ 50 samples (raw threshold alone before that).

## 4. Self-match backtest (`src/backtest.py`)

- Split Ezekiel's recent fills into two disjoint 21-day windows (fallback: halves of all
  history), build a fingerprint from each with the same code path candidates use, and score
  window-A against window-B with `compute_similarity`.
- Rescore all cached candidate fingerprints (`data/candidates/*.json`) against window-B.
- **Pass** = self-match ranks #1 with margin ≥ 0.05 over the best stranger. Report saved to
  `profile/backtest.json`; runs automatically at the end of `fingerprint.main()`
  (non-fatal on failure — prints a loud warning instead).

## 4b. Backtest-adapted thresholds

The first real-data backtest showed self-similarity ≈ 0.53 across a regime change —
config thresholds of 0.90/0.80/0.65 would never fire on the true trader. The scanner now
derives effective thresholds from the last passing backtest's self-score (high =
self − 0.02, medium = self − 0.07, low = self − 0.12, floored above the veto cap), so
alerting tracks what the scorer can actually achieve. Precision comes from the percentile
gate + persistence + vetoes, not from an unreachable raw bar. Candidate persistence uses
the effective low threshold too.

## 5. Alert discipline

- `alert_behavioral_match` fires only when the score clears `similarity_high` on ≥ 2
  consecutive scans (uses the existing candidate `score_history`), plus the percentile gate.
- Combined alerts (fund-flow + behavior) keep single-scan firing — the second vector is
  already independent corroboration.

## Testing

`tests/test_style_matching.py`: synthetic-fill builders; unit tests for each style
sub-profile; veto triggers (scalper vs swing capped below 0.65); None/renormalization
behavior; calibration percentile; self-match backtest passes on identical synthetic styles
and fails on opposite styles.

## Non-goals

Trade-level co-movement matching (approach C) is deferred. No dashboard redesign — new
fields are additive; existing UI keeps working.
