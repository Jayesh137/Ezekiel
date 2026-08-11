"""Read-only diagnosis: where does the behavioural vector's discrimination come from?

Run it after any change to scoring:

    .venv/Scripts/python.exe scripts/diagnose_scorer.py

Nothing is written. It mirrors run_backtest() exactly - same window split,
same fingerprint construction, same resolved thresholds - so its numbers ARE
the backtest's numbers rather than an approximation. Getting that wrong once
produced a badly misleading answer, so the mirroring is load-bearing.

Two questions the system cannot currently answer about itself:

  1. Which dimensions actually separate the target from strangers, and which are
     noise being averaged in?
  2. How much of the measured margin comes from the SIMILARITY SCORE, and how
     much from the style VETO capping strangers at VETO_SCORE_CAP?

Question 2 matters because backtest.json reports best_stranger_score = 0.4500,
which is exactly VETO_SCORE_CAP — so the reported margin may be measuring the
veto's reach rather than the scorer's.

Modifies nothing. Uses the same functions the scanner uses.
"""
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import backtest as bt  # noqa: E402
from src.fingerprint import load_positions_latest  # noqa: E402
from src.scanner import (  # noqa: E402
    VETO_SCORE_CAP,
    _effective_thresholds,
    build_candidate_fingerprint,
    compute_similarity,
)
from src.utils import (  # noqa: E402
    DATA_DIR,
    load_all_records,
    load_config,  # noqa: E402
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

fills = load_all_records(str(DATA_DIR / "fills"))
older, recent = bt.split_windows(fills)
print(f"windows: older {len(older)} fills / recent {len(recent)} fills")

# Mirror run_backtest() exactly, so these numbers are the backtest's numbers.
positions = load_positions_latest()
if isinstance(positions, dict) and "assetPositions" not in positions:
    positions = positions.get("perp", positions)
eff = _effective_thresholds(load_config()["alert_thresholds"])
fp_older = build_candidate_fingerprint(recent, positions)   # backtest's target_fp
fp_recent = build_candidate_fingerprint(older, positions)   # backtest's self_fp

self_score, self_dims, self_ev = compute_similarity(fp_older, fp_recent, eff)
print(f"self-match: {self_score:.4f}  vetoes={self_ev.get('vetoes')}")

scan = json.loads((ROOT / 'data' / 'scans' / 'latest.json').read_text(encoding='utf-8'))
strangers = [r for r in scan.get('results', []) if r.get('fingerprint')]
print(f"strangers with stored fingerprints: {len(strangers)}\n")

rows = []
for r in strangers:
    sfp = r['fingerprint']
    score, dims, ev = compute_similarity(fp_older, sfp, eff)
    vetoed = bool(ev.get('vetoes'))
    # what the score would be WITHOUT the veto cap
    raw = score
    if vetoed and score <= VETO_SCORE_CAP + 1e-9:
        # recompute ignoring vetoes: compute_similarity caps, so recover the
        # uncapped weighted score from the dimensions it returned
        raw = None
    rows.append({'wallet': r['wallet'], 'score': score, 'dims': dims,
                 'vetoed': vetoed, 'raw': raw})

capped = [x for x in rows if x['vetoed']]
clean = [x for x in rows if not x['vetoed']]
print(f"strangers vetoed (score capped at {VETO_SCORE_CAP}): {len(capped)}")
print(f"strangers NOT vetoed:                              {len(clean)}\n")

print("=== Q2: where does the margin come from? ===")
best_any = max(rows, key=lambda x: x['score'])
print(f"  best stranger as scored (veto applied): {best_any['score']:.4f}  {best_any['wallet'][:14]}")
if clean:
    best_clean = max(clean, key=lambda x: x['score'])
    print(f"  best NON-vetoed stranger:               {best_clean['score']:.4f}  {best_clean['wallet'][:14]}")
    print(f"  target self-match:                      {self_score:.4f}")
    m = self_score - best_clean['score']
    print(f"  margin over non-vetoed strangers:       {m:+.4f}"
          f"   <-- {'REAL separation' if m > 0 else 'the SIMILARITY SCORE ALONE DOES NOT SEPARATE'}")
print()

print("=== Q1: per-dimension discriminative power ===")
print("  self  = target's older window vs his own recent window")
print("  strg  = target vs strangers (mean)")
print("  power = self - strg   (<=0 means the dimension is noise or actively misleading)\n")
dim_names = [k for k, v in self_dims.items() if v is not None]
table = []
for k in dim_names:
    svals = [x['dims'].get(k) for x in rows if x['dims'].get(k) is not None]
    if not svals:
        continue
    strg = statistics.fmean(svals)
    table.append((k, self_dims[k], strg, self_dims[k] - strg))
table.sort(key=lambda t: t[3], reverse=True)
print(f"  {'dimension':<22}{'self':>8}{'strg':>8}{'power':>9}")
for k, s, g, p in table:
    flag = '' if p > 0.10 else ('  <- weak' if p > 0 else '  <- NOISE / MISLEADING')
    print(f"  {k:<22}{s:>8.4f}{g:>8.4f}{p:>+9.4f}{flag}")

useful = [t for t in table if t[3] > 0.10]
weak = [t for t in table if 0 < t[3] <= 0.10]
noise = [t for t in table if t[3] <= 0]
print(f"\n  carrying real signal (>0.10): {len(useful)}")
print(f"  weak (0 to 0.10):             {len(weak)}")
print(f"  noise or misleading (<=0):    {len(noise)}"
      + (f"  -> {', '.join(t[0] for t in noise)}" if noise else ""))
