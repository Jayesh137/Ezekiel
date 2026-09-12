# src/correlator.py
"""Re-links the target to a new wallet across a CEX / cross-chain gap.

A sophisticated trader who wants to shake followers won't move funds wallet->wallet
on-chain (we catch that three ways already). He'll withdraw to a CEX, then fund a
FRESH wallet from the CEX and redeposit to Hyperliquid — no direct on-chain link.

Two research-backed heuristics defeat that:

1. Amount + timing (FIFO temporal matching). He exits ~$X at time T and re-enters
   ~$X shortly after. Matching exit amounts to new bridge-deposit amounts in time
   order re-links them even through a CEX. Non-round amounts are far more
   conclusive than round ones (a $1,234,567 match is near-unique; a round $1M
   isn't). FIFO matching is documented to lift linkage rates 15-22pp on mixers.

2. Address reuse (highest-confidence heuristic — cryptographic certainty). A CEX
   deposit address is unique to one account. If a new wallet sends USDC to the
   SAME address the target withdraws to, they are almost certainly the same person.

## Two routes in, two routes out (2026-09-12)

The candidate pool used to be the Arbitrum bridge contract alone. A deposit
through Circle's CCTP — from Ethereum, Base, Solana or any other Circle
chain — never touches it; it arrives as a spot `send` from USDC's linked
contract `0x6b9e7731…`, whose ledger is therefore a complete feed of every
Circle deposit into every account (`src/cctp_feed.py`). The two pools are
read, matched and stored SEPARATELY: the bridge pool costs hundreds of
Etherscan calls and runs daily, the Circle pool is incremental and keyless
and runs every trace. `run_correlation(pools=...)` reads the pools it is
asked for and keeps the stored result of the other.

Exits gained the mirror image: a spot send to the USDC system address is a
Circle withdrawal. One that landed at his own address is not an exit — its
onward L1 movement already is — so only the UNPAIRED ones count, which is
exactly the case where the money left to an address nobody swept.
"""

import argparse
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cctp_feed import refresh_pool, strict_post
from src.chain.collect import records_for
from src.utils import (
    DATA_DIR,
    load_all_records,
    load_config,
    save_latest,
)
from src.withdrawals import cctp_inbound, cctp_withdrawals, match

POOLS = ("bridge", "cctp")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


# Below this many candidate deposits, "this amount is unique" says more about
# the sample size than about the amount, so the shape proxy is the honest read.
MIN_POPULATION_FOR_RARITY = 30


def _uniqueness(amount: float) -> float:
    """How distinctive an amount is, judged by shape alone.

    Round numbers are common — many wallets move exactly $1M — so a round-number
    match is weak and an odd one is strong. This is a *proxy* for rarity, used
    when the real candidate pool is too small to measure against.
    """
    a = round(amount)
    if a <= 0:
        return 0.0
    if a % 1_000_000 == 0:
        return 0.30
    if a % 100_000 == 0:
        return 0.50
    if a % 10_000 == 0:
        return 0.65
    if a % 1_000 == 0:
        return 0.80
    return 1.0


def _rarity(amount: float, population: list[float], tol_pct: float) -> tuple[float, int]:
    """How distinctive an amount is against the deposits actually competing.

    The round-number proxy above asks what an amount looks like. This asks the
    question that actually matters: how many OTHER deposits in this window would
    have matched the same exit just as well? One is near-proof. Fifty is noise,
    however odd the number looks.

    That distinction only became measurable once the candidate pool stopped
    being truncated to the most recent page — see get_recent_bridge_deposits.
    Note the proxy is subsumed rather than contradicted: round amounts recur
    more often in a real population, so they score low here without needing a
    special case.

    Falls back to the proxy when the pool is too small to say anything: with a
    handful of candidates, "unique" is an artefact of the sample, not a fact
    about the amount. Returns (rarity, competitors).
    """
    if len(population) < MIN_POPULATION_FOR_RARITY:
        return _uniqueness(amount), 0
    if amount <= 0:
        return 0.0, 0
    competitors = sum(1 for p in population
                      if p > 0 and abs(p - amount) / amount <= tol_pct)
    if competitors <= 1:
        return 1.0, 0
    return round(1.0 / competitors, 4), competitors - 1


def find_correlations(exits: list[dict], entries: list[dict],
                      tol_pct: float = 0.03, window_days: float = 14,
                      min_amount: float = 100_000, min_confidence: float = 0.55) -> list[dict]:
    """FIFO temporal match of target exits against fresh bridge deposits (entries).

    exits/entries: dicts with 'amount' (USD) and 'ts' (unix seconds). entries also
    carry 'wallet'. Each exit is consumed at most once (FIFO) so one big withdrawal
    can't spawn many spurious findings. Pure function — no I/O, fully testable.
    """
    window_s = window_days * 86400
    # The amounts every candidate deposit is competing with. Measured once, on
    # the whole pool, so rarity is a fact about the window rather than about
    # the order matches happen to be evaluated in.
    population = [float(e.get("amount", 0) or 0) for e in entries]
    usable_exits = sorted(
        [e for e in exits if e.get("amount", 0) >= min_amount and e.get("ts")],
        key=lambda e: e["ts"],
    )
    used = [False] * len(usable_exits)
    findings = []

    for entry in sorted(entries, key=lambda e: e.get("ts", 0)):
        ea = float(entry.get("amount", 0) or 0)
        ets = int(entry.get("ts", 0) or 0)
        if ea < min_amount or not ets:
            continue

        # Best unused exit: preceding the deposit, within window, closest in amount.
        best_i, best_r = None, None
        for i, ex in enumerate(usable_exits):
            if used[i]:
                continue
            xts = ex["ts"]
            if xts > ets or (ets - xts) > window_s:
                continue
            xa = float(ex["amount"])
            r = abs(ea - xa) / xa if xa else 1.0
            if r > tol_pct:
                continue
            if best_r is None or r < best_r:
                best_r, best_i = r, i

        if best_i is None:
            continue

        ex = usable_exits[best_i]
        used[best_i] = True
        r = best_r
        dt_days = (ets - ex["ts"]) / 86400
        amount_score = 1.0 - (r / tol_pct if tol_pct else 0)
        time_score = 1.0 - (dt_days / window_days if window_days else 0)
        uniq, competitors = _rarity(ex["amount"], population, tol_pct)
        confidence = round(0.5 * amount_score + 0.2 * max(0.0, time_score) + 0.3 * uniq, 4)
        if confidence < min_confidence:
            continue

        findings.append({
            "id": f"corr:{entry.get('wallet','')}:{ex.get('ts')}",
            "wallet": entry.get("wallet", ""),
            "deposit_amount_usd": round(ea, 2),
            "exit_amount_usd": round(float(ex["amount"]), 2),
            "amount_diff_pct": round(r * 100, 3),
            "gap_hours": round(dt_days * 24, 1),
            "exit_source": ex.get("source", "unknown"),
            "exit_ref": ex.get("ref", ""),
            "uniqueness": uniq,
            # How many OTHER deposits in the window would have matched this
            # exit just as well. 0 means nothing else came close, which is what
            # makes an amount match evidence rather than coincidence.
            "competing_deposits": competitors,
            "confidence": confidence,
            "deposit_ts": ets,
            "detected_at": utc_now(),
        })

    findings.sort(key=lambda f: f["confidence"], reverse=True)
    return findings


def collect_target_exits(target: str, min_amount: float) -> list[dict]:
    """Target 'exits': HL withdrawals (ledger) + outbound transfers from the substrate.

    The L1 side used to read data/l1_transactions, the single-page Arbitrum-USDC
    table src/tracer.py wrote. Nothing writes that table any more — the tracer was
    pointed at the substrate — so this reads records_for(target) instead, the same
    store every other consumer now reads. That also means the value is already
    computed once, correctly, for every asset on every collected chain: re-deriving
    USDC's `value / 1e6` here would be wrong for anything that isn't USDC, so
    `amount_usd` is taken as-is. A `price_unavailable` record (`amount_usd is None`,
    a known asset like ETH we could not price this run) is skipped outright rather
    than treated as a $0 exit: amount-matching is the entire basis of correlation,
    and an exit of unknown size cannot be matched, correctly, to anything.
    """
    exits = []
    ledger = load_all_records(str(DATA_DIR / "ledger"))

    for entry in ledger:
        d = entry.get("delta", {})
        if d.get("type") != "withdraw":
            continue
        try:
            amt = float(d.get("usdc", 0))
        except (TypeError, ValueError):
            continue
        if amt >= min_amount:
            exits.append({
                "amount": amt,
                "ts": int(entry.get("time", 0)) // 1000,
                "source": "hl_withdraw",
                "ref": entry.get("hash", ""),
            })

    target = target.lower()
    for rec in records_for(target):
        if (rec.get("src") or "").lower() != target:
            continue
        usd = rec.get("amount_usd")
        if usd is None:
            continue
        try:
            amt = float(usd)
        except (TypeError, ValueError):
            # amount_usd is only ever produced internally, but a hand-edited or
            # truncated file in data/transfers/ should skip one bad record, not
            # crash the correlator.
            continue
        if amt >= min_amount:
            exits.append({
                "amount": amt,
                "ts": int(rec.get("ts", 0) or 0),
                "source": "l1_outbound",
                "ref": rec.get("tx_hash", ""),
            })

    exits.extend(unpaired_cctp_exits(target, ledger, min_amount))
    return exits


def unpaired_cctp_exits(target: str, ledger: list[dict], min_amount: float) -> list[dict]:
    """Circle withdrawals that did NOT land at a cluster address.

    A spot send to the USDC system address mints at some recipient minutes
    later. Paired with a mint at his own address it is a transfer to himself
    and its onward movement is already an exit; unpaired, it is money that
    left Hyperliquid to an address this project does not sweep — precisely
    the exit a fresh wallet's deposit should be matched against.
    """
    config = load_config()
    target = (target or "").lower()
    cluster = {target} | {(w or "").lower() for w in config.get("known_self_wallets", []) or []}
    circle = cctp_withdrawals(ledger, target)
    if not circle:
        return []
    inbound = []
    for w in sorted(cluster):
        inbound.extend(cctp_inbound(records_for(w), {w}))
    inbound.sort(key=lambda r: r["ts"])
    m = match(circle, inbound)
    return [{"amount": float(w["gross_usd"]), "ts": int(w["time_s"]),
             "source": "hl_cctp", "ref": w.get("hash", "")}
            for w in m["unmatched"] + m["settling"] if float(w["gross_usd"]) >= min_amount]


def get_recent_bridge_deposits(window_days: float, min_amount: float,
                               budget=None) -> tuple[list[dict], str | None]:
    """Every fresh HL bridge deposit within the window. Returns (deposits, error).

    This used to be one call: `offset=2000, sort=desc`, no pagination. The
    Hyperliquid bridge is among the busiest contracts on Arbitrum, so the most
    recent 2,000 USDC transfers cover hours — against a window that defaults to
    fourteen days. The re-link most likely to actually find a migrated wallet
    was therefore searching a sliver of the candidate pool and reporting
    match_count 0 with no indication that it had only looked at a fraction.

    Now the window's start is converted to a block and walked forward with the
    same paginated reader the substrate uses, so it reads the whole window and
    nothing older. An error is RETURNED rather than swallowed: finding nothing
    because we could not look must never read as finding nothing because there
    was nothing there.
    """
    from src.chain.budget import CallBudget
    from src.chain.chains import chain_by_name
    from src.chain.client import block_at_time, fetch_transfers_to

    if not os.environ.get("ETHERSCAN_API_KEY"):
        return [], "skipped_no_api_key"

    config = load_config()
    chain = chain_by_name("arbitrum", config)
    budget = budget or CallBudget(
        max_calls=(config.get("correlation") or {}).get("max_calls_per_run", 60),
        seconds=(config.get("correlation") or {}).get("time_budget_seconds", 90))

    cutoff = int(time.time()) - int(window_days * 86400)
    start_block, err = block_at_time(chain, cutoff, budget)
    if start_block is None:
        # Walking from 0 instead would read the bridge's entire history and
        # exhaust the budget long before reaching the window we wanted.
        return [], f"could not resolve the window start block: {err}"

    walk, err = fetch_transfers_to(
        config["hl_bridge_contract"], chain, config["usdc_contract_arbitrum"],
        start_block, budget)

    bridge = config["hl_bridge_contract"].lower()
    target = config["target_wallet"].lower()
    excluded = {a.lower() for a in config.get("excluded_addresses", [])}
    excluded |= {a.lower() for a in config.get("known_self_wallets", [])}

    deposits = []
    for row in walk.rows:
        try:
            ts = int(row.get("timeStamp", 0) or 0)
            amt = int(row.get("value", 0) or 0) / 1e6
        except (TypeError, ValueError):
            continue
        if ts < cutoff:
            continue
        if (row.get("to", "") or "").lower() != bridge:
            continue
        frm = (row.get("from", "") or "").lower()
        if not frm or frm == target or frm == bridge or frm in excluded:
            continue
        if amt >= min_amount:
            deposits.append({"wallet": frm, "amount": amt, "ts": ts})

    if walk.truncated and not err:
        err = (f"read {len(walk.rows)} rows and hit the page ceiling before the "
               f"window ended — the candidate pool is incomplete")
    return deposits, err


def get_recent_cctp_deposits(window_days: float, min_amount: float, *, post=None,
                             max_calls: int | None = None,
                             seconds: float | None = None) -> tuple[list[dict], str | None]:
    """Every fresh Circle deposit into Hyperliquid within the window.
    Returns (deposits, error), the same contract as the bridge reader.

    Incremental and keyless: the forwarder's ledger is walked from a stored
    cursor (src/cctp_feed.py). The poster is STRICT — `utils.hl_post` turns a
    failed read into `[]`, and an empty page means "you have reached the
    present" to the walker, so a transport failure would silently advance the
    cursor past unread deposits. A failure here raises inside the walk and is
    returned as an error with the cursor left where it was.
    """
    config = load_config()
    cfg = config.get("correlation") or {}
    target = (config.get("target_wallet") or "").lower()
    excluded = {target}
    excluded |= {(a or "").lower() for a in config.get("known_self_wallets", []) or []}
    excluded |= {(a or "").lower() for a in config.get("excluded_addresses", []) or []}
    deposits, error = refresh_pool(
        post or strict_post, excluded=excluded,
        max_calls=max_calls if max_calls is not None else int(cfg.get("cctp_max_calls", 120)),
        seconds=seconds if seconds is not None else float(cfg.get("cctp_time_budget_seconds", 120)))
    cutoff = int(time.time()) - int(window_days * 86400)
    kept = [d for d in deposits
            if int(d.get("ts") or 0) >= cutoff and float(d.get("amount") or 0) >= min_amount]
    return kept, error


def _stored_pools() -> dict:
    try:
        import json
        with open(DATA_DIR / "correlations" / "latest.json") as f:
            pools = json.load(f).get("pools")
        return pools if isinstance(pools, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def run_correlation(pools=POOLS) -> dict:
    """Gather exits and the requested candidate pools, FIFO-correlate each,
    persist per pool, alert.

    A pool not asked for this run keeps its stored result: the bridge pool is
    read daily (hundreds of Etherscan calls) and the Circle pool on every
    trace (a few keyless calls), and neither run may erase the other's
    matches. `matches` at the top level is the union, which is what the
    roster reads.
    """
    from src.alerts import alert_deposit_correlation

    config = load_config()
    target = config["target_wallet"]
    cfg = config.get("correlation", {})
    min_amount = cfg.get("min_amount_usd", 100_000)
    window_days = cfg.get("window_days", 14)
    tol_pct = cfg.get("tolerance_pct", 0.03)
    min_conf = cfg.get("min_confidence", 0.55)
    pools = tuple(p for p in (pools or POOLS) if p in POOLS)

    exits = collect_target_exits(target, min_amount)
    readers = {"bridge": get_recent_bridge_deposits, "cctp": get_recent_cctp_deposits}
    stored = _stored_pools()
    blocks: dict[str, dict] = {}
    for name in POOLS:
        if name not in pools:
            if name in stored:
                blocks[name] = stored[name]
            continue
        entries, entries_error = readers[name](window_days, min_amount)
        print(f"[correlator] {name}: {len(exits)} exits vs {len(entries)} fresh deposits "
              f"(>= ${min_amount:,.0f}, {window_days}d window)")
        if entries_error:
            # A correlation run that could not see the whole candidate pool has
            # not cleared the target — it has not looked. Saying so is the
            # difference between "no match" and "no idea".
            print(f"[correlator] {name}: INCOMPLETE candidate pool: {entries_error}")
        findings = find_correlations(exits, entries, tol_pct, window_days, min_amount, min_conf)
        for f in findings:
            f["via"] = name
        blocks[name] = {
            "computed_at": utc_now(),
            "candidate_pool_error": entries_error,
            "candidates_considered": len(entries),
            "matches": findings[:50],
        }

    merged = sorted((m for b in blocks.values() for m in b.get("matches") or []),
                    key=lambda f: f.get("confidence", 0), reverse=True)
    errors = [f"{n}: {b['candidate_pool_error']}" for n, b in blocks.items()
              if b.get("candidate_pool_error")]
    result = {
        "computed_at": utc_now(),
        "target": target.lower(),
        "params": {"min_amount_usd": min_amount, "window_days": window_days,
                   "tolerance_pct": tol_pct, "min_confidence": min_conf},
        "pools_read": list(pools),
        "match_count": len(merged),
        # Absent or empty means every pool was whole. Present means a zero
        # match count is not evidence of absence.
        "candidate_pool_error": "; ".join(errors) or None,
        "candidates_considered": sum(b.get("candidates_considered") or 0 for b in blocks.values()),
        "matches": merged[:100],
        "pools": blocks,
    }
    save_latest(str(DATA_DIR / "correlations"), result)

    for name in pools:
        for f in blocks[name]["matches"]:
            if f["confidence"] >= cfg.get("alert_confidence", 0.7):
                alert_deposit_correlation(
                    f["wallet"], f["confidence"], f["deposit_amount_usd"],
                    f["exit_amount_usd"], f["gap_hours"], f["exit_source"], via=name,
                )
    if merged:
        print(f"[correlator] {len(merged)} correlation match(es); top confidence {merged[0]['confidence']}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Re-link target exits to fresh deposits.")
    parser.add_argument("--pools", nargs="+", choices=POOLS, default=list(POOLS),
                        help="candidate pools to read this run (default: all)")
    args = parser.parse_args(argv)
    run_correlation(tuple(args.pools))


if __name__ == "__main__":
    main()
