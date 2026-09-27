# src/correlator.py
"""Re-links the target to a new wallet across a CEX / cross-chain gap.

A sophisticated trader who wants to shake followers won't move funds wallet->wallet
on-chain (we catch that three ways already). He'll withdraw to a CEX, then fund a
FRESH wallet from the CEX and redeposit to Hyperliquid — no direct on-chain link.

Two investigative heuristics can produce leads across that boundary:

1. Amount + timing. Score competing exit/deposit pairs, then select a bounded
   maximum-weight assignment while preserving alternatives. Amount shape and
   rarity are heuristics; neither reveals an exchange's internal routing.

2. Private deposit-address reuse. A repeated route can support association, but
   brokers, payments and delegated operations remain alternative explanations.

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
    have matched the same exit just as well? One is more selective than fifty,
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
    """Score competing hypotheses before deterministic one-to-one assignment.

    Rejected edges never consume an exit. Alternatives remain visible because
    the selected assignment is a useful investigation order, not hidden CEX truth.
    """
    from src.matching import maximum_weight_pairs
    from src.movements import positive_number

    if positive_number(tol_pct) is None or positive_number(window_days) is None:
        return []
    window_s = window_days * 86400

    def usable(rows):
        out = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            amount, ts = positive_number(row.get("amount")), positive_number(row.get("ts"))
            if amount is None or ts is None or amount < min_amount:
                continue
            item = {**row, "amount": amount, "ts": int(ts)}
            key = (item["ts"], str(item.get("wallet", "")), str(item.get("id") or item.get("ref")
                   or item.get("hash") or ""), amount, str(item.get("chain") or ""))
            out[key] = item
        return [out[key] for key in sorted(out)]

    usable_exits, usable_entries = usable(exits), usable(entries)
    population = [row["amount"] for row in usable_entries]
    hypotheses = {}
    by_exit, by_entry = {}, {}
    for i, ex in enumerate(usable_exits):
        rarity, competitors = _rarity(ex["amount"], population, tol_pct)
        for j, entry in enumerate(usable_entries):
            elapsed = entry["ts"] - ex["ts"]
            ratio = abs(entry["amount"] - ex["amount"]) / ex["amount"]
            if elapsed < 0 or elapsed > window_s or ratio > tol_pct:
                continue
            score = round(.5 * (1 - ratio / tol_pct) + .2 * (1 - elapsed / window_s)
                          + .3 * rarity, 4)
            if score < min_confidence:
                continue
            finding = {
                "id": f"corr:{entry.get('wallet', '')}:{ex.get('id') or ex.get('ref') or ex['ts']}",
                "wallet": entry.get("wallet", ""), "deposit_amount_usd": round(entry["amount"], 2),
                "exit_amount_usd": round(ex["amount"], 2), "amount_diff_pct": round(ratio * 100, 3),
                "gap_hours": round(elapsed / 3600, 1), "exit_source": ex.get("source", "unknown"),
                "exit_ref": ex.get("ref", ""), "exit_id": ex.get("id"),
                "event_ids": ex.get("event_ids", [ex.get("ref", "")]),
                "deposit_ref": entry.get("ref") or entry.get("hash"),
                "uniqueness": rarity, "competing_deposits": competitors,
                "confidence": score, "score_kind": "heuristic", "deposit_ts": entry["ts"],
            }
            hypotheses[i, j] = finding
            by_exit.setdefault(i, []).append((i, j))
            by_entry.setdefault(j, []).append((i, j))
    pairs, mode = maximum_weight_pairs([(i, j, row["confidence"])
                                       for (i, j), row in hypotheses.items()])
    findings = []
    for i, j in pairs:
        finding = hypotheses[i, j].copy()
        alternatives = sorted((hypotheses[p] for p in set(by_exit[i] + by_entry[j])
                               if p != (i, j)), key=lambda row: (-row["confidence"], row["id"]))
        finding.update(assignment_mode=mode, alternative_count=len(alternatives),
                       alternatives=[{k: row[k] for k in ("wallet", "confidence", "exit_ref", "deposit_ts")}
                                     for row in alternatives[:10]],
                       ambiguous=bool(alternatives), detected_at=utc_now())
        findings.append(finding)
    return sorted(findings, key=lambda row: (-row["confidence"], row["id"]))


def collect_target_movements(target: str, min_amount: float = 0, *,
                             config: dict | None = None, data_dir: Path | None = None) -> dict:
    """Read the trusted cluster and reconcile exact routes, without writing data."""
    import json

    from src.movements import reconcile_movements

    config = config if config is not None else load_config()
    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    cluster = {target.lower()} | {str(w).lower() for w in config.get("known_self_wallets", []) if w}
    records = [record for wallet in sorted(cluster) for record in records_for(wallet)]
    ledger = load_all_records(str(directory / "ledger"))
    try:
        decodes = json.loads((directory / "labels" / "bridge_decodes.json").read_text())
    except (OSError, ValueError):
        decodes = {}
    if not isinstance(decodes, dict):
        decodes = {}
    try:
        bindings = json.loads((directory / 'routes' / 'bindings.json').read_text())
    except (OSError, ValueError):
        bindings = {}
    if isinstance(bindings, dict):
        records = [{**row, 'route_decode': row.get('route_decode') or bindings.get(row.get('id'))}
                   for row in records]
    result = reconcile_movements(records, ledger, cluster, decodes, min_amount)
    circle = unpaired_cctp_exits(target, ledger, min_amount, config=config)
    result["unresolved_exits"].extend({**row, "id": f"hl-cctp:{row['ref']}",
                                      "event_ids": [f"hl:{row['ref']}"],
                                      "resolution": "unresolved", "route_resolved": False}
                                     for row in circle)
    result["coverage"]["cctp_pairing"] = "legacy_amount_time_hypothesis"
    result["coverage"]["known_cluster_wallets"] = len(cluster)
    result["totals"]["unresolved_exit_usd"] = round(sum(row["amount"] for row in result["unresolved_exits"]), 2)
    return result


def collect_target_exits(target: str, min_amount: float) -> list[dict]:
    return collect_target_movements(target, min_amount)["unresolved_exits"]


def unpaired_cctp_exits(target: str, ledger: list[dict], min_amount: float, *,
                        config: dict | None = None) -> list[dict]:
    """Circle withdrawals that did NOT land at a cluster address.

    A spot send to the USDC system address mints at some recipient minutes
    later. Paired with a mint at his own address it is a transfer to himself
    and its onward movement is already an exit; unpaired, it is money that
    left Hyperliquid to an address this project does not sweep — precisely
    the exit a fresh wallet's deposit should be matched against.
    """
    config = config if config is not None else load_config()
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
    """The per-pool blocks last written, migrating a pre-`pools` file once.

    Before the split there was one pool — the Arbitrum bridge — and its answer
    was the flat `matches` list. A `--pools cctp` run preserves what it did not
    read by copying `stored[name]`, so against that older shape it preserved
    nothing and overwrote the bridge's last answer with silence. Measured live
    2026-09-12: 7-10 matches every run up to 02:14, then 0 on every run after
    the split, with `pools` holding `cctp` alone. Nothing had cleared the bridge
    pool; it had simply stopped being asked, and its previous answer was gone.

    Keyed on the ABSENCE of a bridge block rather than on the presence of the
    legacy list — the same rule the alert shards follow. `latest.json` is
    rewritten carrying both shapes, so a migration keyed the other way would
    fold its own output back in on every subsequent run.
    """
    try:
        import json
        with open(DATA_DIR / "correlations" / "latest.json") as f:
            doc = json.load(f)
        pools = doc.get("pools")
        pools = dict(pools) if isinstance(pools, dict) else {}
        if "bridge" not in pools and isinstance(doc.get("matches"), list):
            pools["bridge"] = {
                "computed_at": doc.get("computed_at"),
                "candidate_pool_error": doc.get("candidate_pool_error"),
                "candidates_considered": doc.get("candidates_considered") or 0,
                "matches": [{**m, "via": m.get("via") or "bridge"}
                            for m in doc["matches"] if isinstance(m, dict)],
            }
        return pools
    except (OSError, ValueError, AttributeError):
        return {}


def run_correlation(pools=POOLS) -> dict:
    """Gather exits and the requested candidate pools, assign hypotheses,
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
