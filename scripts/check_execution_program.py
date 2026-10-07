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
from src.utils import DATA_DIR, atomic_write_json, load_config, save_latest

OUT_DIR = DATA_DIR / "execution_program"
MAX_CANDIDATES = 60
# Timeouts nest: this read budget + the ~12s signature load + the save must stay
# under the step's `timeout-minutes: 7` in trace.yml. It was 600s inside a 4-minute
# step, so the step was killed on every run from 2026-09-29 and never once wrote a
# report. userFills paces at ~12s a wallet (2,000 rows weigh ~100), so a run reads
# ~25 of the 60; `order_by_staleness` rotates the rest in on the following runs.
READ_BUDGET_SECONDS = 300
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
    try:
        with open(OUT_DIR / "census.json") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def select_candidates(roster_candidates, discovery, clip_coins, cap=MAX_CANDIDATES):
    """Roster leads plus fresh wallets seen trading his clipped coins.

    A migration wallet is small and new — outside the leaderboard and off the
    roster for a long time — so checking only roster candidates would find it late
    or never. The discovery tape sees whoever trades his markets; a wallet trading
    a coin he runs a fixed clip on is exactly where his script would surface first.
    A slice of the budget is reserved for these so roster leads cannot crowd them
    out (the "reserve exploration capacity" lesson). Deterministic and deduped.
    """
    clip_coins = {c for c in (clip_coins or set())}
    ordered_roster = [str(w).lower() for w in (roster_candidates or [])]
    fresh = []
    for row in (discovery or {}).get("candidates", []):
        wallet = (row.get("wallet") or "").lower()
        markets = set((row.get("markets") or {}).keys())
        if wallet and markets & clip_coins:
            fresh.append((wallet, row.get("trade_count") or 0))
    fresh.sort(key=lambda wc: (-wc[1], wc[0]))
    fresh_wallets = [w for w, _ in fresh]
    reserved = max(0, cap // 2)
    picked, seen = [], set()

    def add(wallet):
        if wallet and wallet not in seen:
            seen.add(wallet)
            picked.append(wallet)

    # Roster leads first (they carry evidence), leaving the reserved tail for fresh.
    for wallet in ordered_roster[:cap - min(reserved, len(fresh_wallets))]:
        add(wallet)
    for wallet in fresh_wallets:
        if len(picked) >= cap:
            break
        add(wallet)
    for wallet in ordered_roster:  # backfill any reserved room fresh did not use
        if len(picked) >= cap:
            break
        add(wallet)
    return picked[:cap]


def load_previous():
    try:
        with open(OUT_DIR / "latest.json") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def load_discovery():
    try:
        with open(DATA_DIR / "discovery" / "latest.json") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def candidate_wallets(config, roster, clip_coins):
    from src.roster import detector_candidates
    roster_candidates = detector_candidates(config, roster, MAX_CANDIDATES)
    return select_candidates(roster_candidates, load_discovery(), clip_coins, MAX_CANDIDATES)


def order_by_staleness(wallets, last_checked):
    """Never-read wallets first, then the longest unread; ties keep selection order.

    The read budget covers only part of the list, so a fixed order would read the
    same head every run and never reach the reserved fresh-wallet tail — the cap
    starving exactly the wallets with no evidence yet.
    """
    last_checked = last_checked or {}
    return sorted(wallets, key=lambda w: last_checked.get(w) or "")


def carry_forward(previous, wallets, read_wallets, census):
    """Matches from earlier runs for candidates this run did not reach.

    A wallet left unread for lack of budget is unknown this run, not unmatched:
    dropping its row would lapse its roster vote between reads. Discrimination is
    recomputed against the current census; `carried_forward` marks the row so it
    is never routed as an alert a second time.
    """
    keep = set(wallets) - set(read_wallets)
    out = []
    for match in (previous or {}).get("matches", []):
        wallet = (match.get("wallet") or "").lower()
        if wallet in keep:
            out.append({**match, "carried_forward": True,
                        "discriminating": ep.is_discriminating(match, census)})
    return out


def roster_vector_map(roster):
    """Each roster wallet's vectors. Roster rows are keyed `wallet`; `address` is the
    older spelling, still read."""
    out = {}
    for row in (roster or {}).get("wallets", []):
        addr = (row.get("wallet") or row.get("address") or "").lower()
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
        if not match.get("discriminating") or match.get("carried_forward"):
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
    clip_coins = set((target_sig.get("clip_table") or {}).keys())
    wallets = candidate_wallets(config, roster, clip_coins)
    previous = load_previous()
    last_checked = {w: t for w, t in ((previous or {}).get("last_checked") or {}).items()
                    if w in set(wallets)}
    rows, errors = [], []
    with ReadBudget(seconds=READ_BUDGET_SECONDS, weight_per_minute=600) as budget:
        for wallet in order_by_staleness(wallets, last_checked):
            if not budget.can_continue():
                break
            result = hl_read({"type": "userFills", "user": wallet})
            if not result.get("ok"):
                if budget.stopped_reason == "time_budget":
                    break  # refused by the budget, not a failed read
                errors.append({"wallet": wallet, "error": result.get("error")})
                continue
            rows.append({"wallet": wallet, "signature": ep.signature(result["data"])})
            last_checked[wallet] = datetime.now(UTC).isoformat()
        budget_report = budget.report()
    report = build_report(target_sig, rows, census, roster_vector_map(roster),
                          target_wallet=target_wallet)
    read_wallets = {r["wallet"] for r in rows}
    report["matches"].extend(carry_forward(previous, wallets, read_wallets, census))
    report["errors"] = errors
    report["wallets_checked"] = len(rows)
    report["candidates"] = len(wallets)
    report["never_checked"] = sum(1 for w in wallets if w not in last_checked)
    report["last_checked"] = last_checked
    report["budget"] = budget_report
    report["target_signature"] = {"clip_table": target_sig.get("clip_table"),
                                  "ioc_5pct_share": target_sig.get("ioc_5pct_share"),
                                  "program_runs": target_sig.get("program_runs")}
    save_latest(str(OUT_DIR), report)
    atomic_write_json(OUT_DIR / "target.json", {"computed_at": report["computed_at"], **target_sig})
    for severity, match in decide_alerts(report):
        alert_execution_program_match(match["wallet"], match, severity)
    print(f"[execution-program] checked {len(rows)} of {len(wallets)} wallets "
          f"({report['never_checked']} never read, stopped: {budget_report['stopped_reason']}), "
          f"{len(report['matches'])} measured, {len(decide_alerts(report))} routed, "
          f"census={'yes' if census else 'no'}")
    return report


if __name__ == "__main__":
    main()
    time.sleep(0)
