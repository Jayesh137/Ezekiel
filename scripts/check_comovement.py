#!/usr/bin/env python3
"""Who moves first: roster candidates against the target's recent decisions.

A copier follows the target's fills; a second account run by the same hand
moves with them or before them. One fills call per candidate over the same
21-day window the scanner uses. Evidence for the roster, and a HIGH alert on
a same-hand verdict — the one behavioural reading a copy-trader cannot fake.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import UTC, datetime

from src.alerts import alert_same_hand
from src.comovement import (
    COMOVEMENT_DIR,
    PAIR_WINDOW_MIN,
    decisions,
    save,
    score_decisions,
)
from src.fingerprint import load_fills
from src.utils import DATA_DIR, hl_post, load_config

LOOKBACK_DAYS = 21
MAX_CANDIDATES = 40


def candidate_wallets(config: dict) -> list[str]:
    target = (config.get("target_wallet") or "").lower()
    out, seen = [], {target}
    for source, key in (("roster", "wallets"), ("newborn", "newborn")):
        try:
            with open(DATA_DIR / source / "latest.json") as f:
                rows = json.load(f).get(key, [])
        except (OSError, ValueError, AttributeError):
            rows = []
        for row in rows:
            a = (row.get("wallet") or "").lower()
            if not a or a in seen or row.get("tier") == "INFRASTRUCTURE":
                continue
            seen.add(a)
            out.append(a)
    return out[:MAX_CANDIDATES]


MAX_HISTORY = 5000


def remember_decisions(wallet: str, fresh: list[dict], cutoff_ms: float) -> list[dict]:
    """Merge this run's decisions into the wallet's stored history; return it."""
    path = COMOVEMENT_DIR / "history" / f"{wallet.lower()}.json"
    try:
        with open(path) as f:
            stored = json.load(f)
    except (OSError, ValueError):
        stored = []
    by_key = {(d["coin"], d["side"], d["start"]): d for d in stored if isinstance(d, dict)}
    for d in fresh:
        by_key[(d["coin"], d["side"], d["start"])] = d
    merged = sorted((d for d in by_key.values() if d["start"] >= cutoff_ms),
                    key=lambda d: d["start"])[-MAX_HISTORY:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(merged, f)
    return merged


def main() -> int:
    config = load_config()
    cutoff = (time.time() - LOOKBACK_DAYS * 86400) * 1000
    target_fills = [f for f in load_fills() if (f.get("time") or 0) >= cutoff]
    if not target_fills:
        print("[comovement] the target has no fills in the window — nothing to compare")
        return 0

    # userFills returns the NEWEST 2,000 fills; userFillsByTime the OLDEST
    # 2,000 after its start. For a busy candidate the latter covers the first
    # hours of the window and never overlaps the target's recent sessions
    # (measured: seven candidates, zero pairs). So the newest fills are taken,
    # and the target's decisions are compared only across the span they cover.
    candidates = {}
    spans = {}
    for wallet in candidate_wallets(config):
        try:
            fills = hl_post({"type": "userFills", "user": wallet})
        except Exception as exc:                      # noqa: BLE001 - transport
            print(f"[comovement] {wallet[:12]}... unreadable: {type(exc).__name__}")
            continue
        fills = [f for f in (fills if isinstance(fills, list) else [])
                 if (f.get("time") or 0) >= cutoff]
        if fills:
            candidates[wallet] = fills
            times = [f["time"] for f in fills]
            spans[wallet] = (min(times), max(times))
        time.sleep(0.12)

    # A busy candidate's newest 2,000 fills cover hours, and the target may be
    # quiet for days, so one read rarely overlaps. Decisions (not fills) are
    # kept per wallet across runs, so coverage grows every half hour and a
    # candidate is eventually compared over the whole window.
    results = {}
    target_decisions = decisions(target_fills)
    for wallet, fills in candidates.items():
        history = remember_decisions(wallet, decisions(fills), cutoff)
        if not history:
            continue
        lo = min(d["start"] for d in history)
        hi = max(d["end"] for d in history)
        pad = PAIR_WINDOW_MIN * 60_000
        in_span = [d for d in target_decisions if lo - pad <= d["start"] <= hi + pad]
        results[wallet] = score_decisions(in_span, history)
        results[wallet]["candidate_span"] = [lo, hi]
        results[wallet]["decisions_on_record"] = len(history)
    report = {"computed_at": datetime.now(UTC).isoformat(),
              "candidates_scored": len(results), "results": results}
    save(report)
    print(f"[comovement] {report['candidates_scored']} candidate(s) scored against "
          f"{len(target_fills)} target fills over {LOOKBACK_DAYS}d")
    for wallet, r in list(report["results"].items())[:10]:
        print(f"[comovement]   {wallet[:12]}... {r['verdict']:<12} pairs={r.get('pairs', 0):<4} "
              f"lead={r.get('lead_share')} excess={r.get('excess')} — {r['reason']}")
        if r["verdict"] == "same_hand":
            alert_same_hand(wallet, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
