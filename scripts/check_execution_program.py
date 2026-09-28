#!/usr/bin/env python3
"""Detect the trader's execution program (his SDK slicer) on other wallets.

The target trades through hyperliquid-python-sdk `market_open`: taker IOC orders
at one round base size per coin, fired ~1.7s apart in long runs, limit priced 5%
through the book, no client id. The size table per coin (ZEC 1, SILVER 20, NEAR
250, BTC 0.1…) is what travels to a new wallet. This script builds his signature
from stored fills/orders, fetches each candidate's recent fills, and reports which
reproduce his clip table.

A clip-table match is a BEHAVIOUR signal (a shared bot/frontend is the confounder,
rule 9), so it corroborates and never confirms alone, and only after the census
(`scripts/census_execution_program.py`) has MEASURED that the match is rare (rule
4). Until `data/execution_program/census.json` exists, matches are recorded as
context and route nothing. A match already carrying an independent financial or
protocol vector is two vectors agreeing → CRITICAL; a rare match alone → HIGH.

The pure policy (`build_report`, `decide_alerts`) is unit-tested; `main` is the
network/alert shell.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import execution_program as ep
from src.utils import DATA_DIR, load_config, save_latest

OUT_DIR = DATA_DIR / "execution_program"
MAX_CANDIDATES = 60
# Financial/protocol vectors — an execution match beside one of these is the
# two-independent-vectors case. A behavioural sibling does not make it CRITICAL.
INDEPENDENT_VECTORS = {"transfer", "linkage", "hl_native", "correlation",
                       "shared_agent", "explicit_link", "dormancy_handoff", "referral"}


def load_target_signature(*, fills=None, orders=None):
    """His signature from stored fills+orders, cached for offline use."""
    from src.fingerprint import load_fills, load_orders
    fills = load_fills() if fills is None else fills
    orders = load_orders() if orders is None else orders
    sig = ep.signature(fills, orders)
    return sig


def load_census():
    path = OUT_DIR / "census.json"
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def candidate_wallets(config, roster):
    from src.roster import detector_candidates
    return detector_candidates(config, roster, MAX_CANDIDATES)


def roster_vector_map(roster):
    out = {}
    for row in (roster or {}).get("wallets", []):
        addr = (row.get("address") or "").lower()
        if addr:
            out[addr] = set(row.get("vectors") or [])
    return out


def build_report(target_sig, candidate_rows, census, roster_vectors, *, target_wallet=None):
    """Rank candidates against the target and annotate each match's disposition."""
    target_wallet = (target_wallet or "").lower()
    rows = [r for r in candidate_rows if (r.get("wallet") or "").lower() != target_wallet]
    ranked = ep.rank_matches(target_sig, rows)
    matches = []
    for match in ranked:
        wallet = (match.get("wallet") or "").lower()
        vectors = roster_vectors.get(wallet, set())
        matches.append({
            **match,
            "discriminating": ep.is_discriminating(match, census),
            "has_independent_vector": bool(vectors & INDEPENDENT_VECTORS),
        })
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target_clip_count": len(target_sig.get("clip_table") or {}),
        "census_present": census is not None,
        "census_population": (census or {}).get("population"),
        "matches": matches,
    }


def decide_alerts(report):
    """Route only measured-rare matches. Two vectors → CRITICAL, one → HIGH."""
    out = []
    for match in report.get("matches", []):
        if not match.get("discriminating"):
            continue
        out.append((("CRITICAL" if match.get("has_independent_vector") else "HIGH"), match))
    return out


def roster_execution_matches(report):
    """Wallets whose match is discriminating — the roster reads this to vote."""
    if not report:
        return {}
    return {(m.get("wallet") or "").lower(): m for m in report.get("matches", [])
            if m.get("discriminating")}


def main():
    from src.alerts import alert_execution_program_match
    from src.hl_budget import ReadBudget
    from src.utils import hl_read

    config = load_config()
    target_wallet = config.get("target_wallet", "")
    target_sig = load_target_signature()
    census = load_census()
    try:
        with open(DATA_DIR / "roster" / "latest.json") as handle:
            roster = json.load(handle)
    except (OSError, ValueError):
        roster = {}
    wallets = candidate_wallets(config, roster)
    rows, errors = [], []
    with ReadBudget(seconds=600, weight_per_minute=600) as budget:
        for wallet in wallets:
            if not budget.can_continue():
                break
            result = hl_read({"type": "userFills", "user": wallet})
            if not result.get("ok"):
                errors.append({"wallet": wallet, "error": result.get("error")})
                continue
            rows.append({"wallet": wallet, "signature": ep.signature(result["data"])})
    report = build_report(target_sig, rows, census, roster_vector_map(roster),
                          target_wallet=target_wallet)
    report["errors"] = errors
    report["wallets_checked"] = len(rows)
    report["target_signature"] = {"clip_table": target_sig.get("clip_table"),
                                  "ioc_5pct_share": target_sig.get("ioc_5pct_share"),
                                  "program_runs": target_sig.get("program_runs")}
    save_latest(str(OUT_DIR), report)
    save_latest(str(OUT_DIR / "target"), {"computed_at": report["computed_at"], **target_sig})
    for severity, match in decide_alerts(report):
        alert_execution_program_match(match["wallet"], match, severity)
    print(f"[execution-program] checked {len(rows)} wallets, "
          f"{len(report['matches'])} measured, {len(decide_alerts(report))} routed, "
          f"census={'yes' if census else 'no'}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
