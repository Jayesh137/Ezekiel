# src/tracer.py
"""Traces fund flows on Arbitrum L1 to detect wallet migrations."""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import thresholds as th
from src.alerts import alert_combined_match, alert_fund_movement, alert_new_wallet_found
from src.chain.budget import CallBudget
from src.chain.chains import enabled_chains
from src.chain.collect import records_for, sweep_wallet
from src.utils import (
    DATA_DIR,
    append_records,
    candidate_current_score,
    etherscan_get,
    load_config,
    save_latest,
)

# Max unique destinations to trace per run — a safety net so a wallet spammed
# with transfers to many addresses can never blow the job timeout.
MAX_DESTINATIONS = 50

# Fallback chain label for a substrate record that somehow lacks one. Every
# real record carries `chain`; this only matters for hand-built test fixtures.
CHAIN_DEFAULT = "arbitrum"

# Wall-clock budget for the tracing loop. The CI job has a 5-minute hard
# timeout; stop tracing new destinations after this so partial findings still
# get saved and committed instead of the job being cancelled.
TRACE_BUDGET_SECONDS = 240

# Run-scoped cache of Etherscan transfer lookups, keyed by (address, start_block).
# The same address gets looked up repeatedly (find_hl_deposits + next-hop), so
# caching avoids redundant rate-limited API calls. Cleared at the start of a run.
_transfer_cache: dict[tuple[str, int], list[dict]] = {}

# The incremental gate. Before the substrate landed, novelty came from
# read_cursor("last_l1_block"): only transfers newer than the cursor were
# returned, and the cursor then advanced. records_for() has no such notion — it
# returns every record ever stored — so without this marker every scheduled run
# re-traces the whole history: up to MAX_DESTINATIONS "CRITICAL: Fund Movement
# Detected" emails every 24 hours forever (the alert cooldown is the only other
# brake), plus a find_hl_deposits round trip per destination every 30 minutes.
#
# Record ids rather than a block or timestamp high-water mark, because
# unique_destinations orders by VALUE and truncates at MAX_DESTINATIONS: what a
# run actually processes is not a contiguous prefix of anything, and a
# positional marker would therefore have to either skip the untraced tail
# permanently or re-offer the traced head forever.
TRACED_MARKER = "traced_outbound.json"


def _traced_path() -> Path:
    return Path(DATA_DIR) / "state" / TRACED_MARKER


def _load_traced() -> dict:
    try:
        doc = json.loads(_traced_path().read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _save_traced(doc: dict) -> None:
    path = _traced_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True))


def untraced_outbound(wallet: str, rows: list[dict]) -> list[dict]:
    """`rows` minus every record an earlier run already finished tracing.

    First run decision — the marker is absent but the substrate is already deep
    (the backfill recovers history back past 2025-11-30). Seeding it to
    everything currently stored, and alerting on nothing, is chosen over
    alerting on all of it: the alternative is a burst of up to MAX_DESTINATIONS
    CRITICAL emails about months-old transfers on the first scheduled run after
    merge, which is the exact failure this gate exists to prevent. Nothing is
    discarded by seeding — the records stay in data/transfers/, on the transfer
    graph, and in whatever fund_flows findings earlier runs already recorded.
    Only the email is suppressed, and only for movements that predate the gate.

    The seed is written loudly and stamped in the marker file rather than done
    silently, because "we chose not to look at this" must be legible on disk.
    """
    wl = (wallet or "").lower()
    doc = _load_traced()
    entry = doc.get(wl)
    if entry is None:
        ids = sorted({r["record_id"] for r in rows if r.get("record_id")})
        doc[wl] = {"seeded_at": utc_now(), "seeded": len(ids), "traced": ids}
        _save_traced(doc)
        print(f"[tracer] First run of the incremental gate for {wallet}: "
              f"{len(ids)} stored outbound record(s) marked as already-seen. "
              f"They remain in data/transfers/ and on the transfer graph; "
              f"only movements from here on will alert.")
        return []
    already = set(entry.get("traced") or [])
    return [r for r in rows if r.get("record_id") not in already]


def mark_traced(wallet: str, record_ids, *, known_ids=None) -> None:
    """Advance the marker over the records this run actually finished.

    `known_ids` bounds the marker to records the substrate still holds, so it
    can never grow past the outbound history it is tracking. It is ignored when
    empty: an intersection against a transiently unreadable substrate would
    erase the marker and re-alert everything on the next run.
    """
    wl = (wallet or "").lower()
    doc = _load_traced()
    entry = doc.setdefault(wl, {"traced": []})
    merged = set(entry.get("traced") or []) | {i for i in record_ids if i}
    if known_ids:
        merged &= set(known_ids)
    entry["traced"] = sorted(merged)
    entry["last_traced_at"] = utc_now()
    _save_traced(doc)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def get_usdc_transfers(address: str, start_block: int = 0) -> list[dict]:
    """Get all USDC token transfers for an address on Arbitrum."""
    cache_key = (address.lower(), start_block)
    if cache_key in _transfer_cache:
        return _transfer_cache[cache_key]

    config = load_config()
    result = etherscan_get({
        "module": "account",
        "action": "tokentx",
        "address": address,
        "contractaddress": config["usdc_contract_arbitrum"],
        "startblock": start_block,
        "endblock": 99999999,
        "page": 1,
        "offset": 1000,
        "sort": "desc",
    })
    status = result.get("status")
    message = result.get("message", "")
    transfers = result.get("result", [])

    if status == "1" and isinstance(transfers, list):
        print(f"[tracer] Etherscan: {len(transfers)} USDC transfers found for {address[:10]}...")
        _transfer_cache[cache_key] = transfers
        return transfers
    elif status == "0" and message == "No transactions found":
        print(f"[tracer] Etherscan: No USDC transfers for {address[:10]}... (confirmed empty)")
        _transfer_cache[cache_key] = []
        return []
    else:
        # Transient API error — don't cache, so a later lookup can retry.
        print(f"[tracer] Etherscan API issue: status={status}, message={message}, result_type={type(transfers).__name__}")
        return []


def _as_etherscan_row(rec: dict) -> dict | None:
    """A substrate record in the row shape the finding builders already read.

    unique_destinations and build_finding index `to`, `value` and `hash`, and
    value is expected in 6-decimal USDC units. Converting here keeps the whole
    downstream alert path — which the dashboard and the combined-alert route
    depend on — byte-identical.

    Returns None for a record whose `amount_usd` is None. That only happens
    for `value_basis == "price_unavailable"`: a known major asset (e.g. ETH)
    that could not be priced this run. spam.classify_spam deliberately leaves
    that record un-quarantined rather than lose "a potentially large real
    transfer on the strength of a price outage" (its own words). Collapsing
    the missing price to 0 here would undo that protection one layer up: the
    record would carry value "0", unique_destinations' dust filter would drop
    it, and a real transfer would go unlooked-at — exactly the "zero is
    invisible" failure src/chain/assets.py's value_usd docstring warns about.
    Returning None instead excludes it from this run's trace without
    fabricating a dollar figure nobody has; the record stays on disk,
    unquarantined, for a future run to re-price.
    """
    usd = rec.get("amount_usd")
    if usd is None:
        return None
    try:
        value = str(int(round(float(usd) * 1e6)))
    except (TypeError, ValueError):
        # amount_usd is only ever produced internally as a float or None, but a
        # hand-edited or truncated file in data/transfers/ should degrade to
        # skipping one bad record, not crash the whole sweep.
        return None
    return {
        "to": rec.get("dst", ""),
        "from": rec.get("src", ""),
        "value": value,
        "hash": rec.get("tx_hash", ""),
        "blockNumber": str(rec.get("block", 0)),
        "timeStamp": str(rec.get("ts", 0)),
        "tokenSymbol": rec.get("asset", ""),
        "chain": rec.get("chain", CHAIN_DEFAULT),
        # Additive, and not part of the Etherscan row shape: the substrate id
        # this row came from, so the incremental gate can mark exactly the
        # records a run finished. Nothing downstream reads it.
        "record_id": rec.get("id"),
    }


def get_normal_transactions(address: str, start_block: int = 0) -> list[dict]:
    """Get all normal transactions for an address on Arbitrum."""
    result = etherscan_get({
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": 99999999,
        "page": 1,
        "offset": 1000,
        "sort": "desc",
    })
    if result.get("status") == "1" and result.get("result"):
        return result["result"]
    return []


def find_hl_deposits(address: str) -> list[dict]:
    """Check if an address has deposited to the Hyperliquid bridge."""
    config = load_config()
    transfers = get_usdc_transfers(address)
    bridge = config["hl_bridge_contract"].lower()
    return [t for t in transfers if t.get("to", "").lower() == bridge]


def check_if_hl_deposit(address: str) -> bool:
    """Check if an address has deposited to the Hyperliquid bridge."""
    return bool(find_hl_deposits(address))


def trace_outbound_transfers(wallet: str) -> list[dict]:
    """Find transfers OUT from the tracked wallet, on every collected chain.

    Collection is delegated to the substrate, which paginates properly and
    quarantines poisoning; this function is now only about selecting the
    outbound side of it.
    """
    config = load_config()
    budget = CallBudget(
        max_calls=(config.get("collection") or {}).get("max_calls_per_run", 2500),
        seconds=(config.get("collection") or {}).get("time_budget_seconds", 420),
    )
    sweep_wallet(wallet, enabled_chains(config), budget, cluster=True)

    wl = (wallet or "").lower()
    rows = (_as_etherscan_row(r) for r in records_for(wl)
            if (r.get("src") or "").lower() == wl)
    return [row for row in rows if row is not None]


def save_fund_flow_findings(findings: list[dict]) -> None:
    """Persist fund-flow findings without clobbering behavioral scan results."""
    if not findings:
        return
    append_records(str(DATA_DIR / "fund_flows"), findings, key_field="id")

    existing = []
    latest_path = DATA_DIR / "fund_flows" / "latest.json"
    if latest_path.exists():
        try:
            import json
            with open(latest_path) as f:
                current = json.load(f)
            existing = current.get("findings", [])
        except Exception:
            existing = []

    by_id = {f.get("id"): f for f in existing if f.get("id")}
    for finding in findings:
        by_id[finding["id"]] = finding

    merged = sorted(by_id.values(), key=lambda f: f.get("detected_at", ""), reverse=True)
    save_latest(str(DATA_DIR / "fund_flows"), {
        "last_updated": utc_now(),
        "findings": merged[:100],
    })


def build_finding(source: str, destination: str, amount_usdc: float, tx_hash: str,
                  method: str, hop_count: int, deposited_to_hl: bool,
                  bridge_tx_hash: str | None = None,
                  asset: str = "USDC", chain: str = CHAIN_DEFAULT) -> dict:
    """`asset`/`chain` are additive fields, not a rename: `amount_usdc` and
    `amount_usdc_raw` keep their names (the dashboard reads those keys) even
    though the dollar value they hold may now come from a non-USDC transfer.
    The defaults match this function's only callers that don't pass them —
    the hop-2/hop-3 findings in trace_fund_flow, which are still genuinely
    Arbitrum USDC, sourced from get_usdc_transfers rather than the substrate.
    """
    return {
        "id": f"{method}:{tx_hash}:{destination}",
        "source": source,
        "destination": destination,
        "amount_usdc": value_to_display(amount_usdc),
        "amount_usdc_raw": amount_usdc,
        "tx_hash": tx_hash,
        "bridge_tx_hash": bridge_tx_hash,
        "method": method,
        "hop_count": hop_count,
        "deposited_to_hl": deposited_to_hl,
        "confidence": 1.0 if method == "direct_fund_trace" else 0.9,
        "status": "NEW_WALLET_CANDIDATE" if deposited_to_hl else "PENDING_HL_DEPOSIT",
        "detected_at": utc_now(),
        "asset": asset,
        "chain": chain,
    }


def value_to_display(value: float) -> str:
    return f"{value:,.2f}"


def is_traceable(transfer: dict, wallet: str) -> bool:
    """Could this transfer ever be traced at all?

    unique_destinations' own filter, named so the incremental gate can tell a
    destination it deferred (must be retried) from a row it will never trace
    (must be marked, or the wallet stays permanently "dirty" and the
    no-new-transfers message never prints again).
    """
    dest = transfer.get("to", "")
    if not dest or dest.lower() == wallet.lower():
        return False
    return int(transfer.get("value", 0)) > 0


def unique_destinations(outbound: list[dict], wallet: str) -> list[dict]:
    """Collapse outbound transfers to one representative per destination.

    Drops zero-value transfers (address-poisoning dust moves no funds) and
    self-transfers, and keeps only the highest-value transfer per destination so
    each destination is traced exactly once. Without this, a wallet spammed with
    hundreds of 0-USDC transfers to the same address triggers hundreds of
    identical Etherscan/SMTP round trips and blows the job timeout.

    The MAX_DESTINATIONS cap is a per-run cap, not a ceiling: destinations that
    fall outside it are left unmarked by the incremental gate and come back on
    the next run. Against the full stored history it WOULD be a permanent
    ceiling — once fifty historical destinations outranked a genuinely new
    smaller movement, that movement would never be traced at all.
    """
    best: dict[str, dict] = {}
    for t in outbound:
        if not is_traceable(t, wallet):
            continue
        key = t["to"].lower()
        if key not in best or int(t.get("value", 0)) > int(best[key].get("value", 0)):
            best[key] = t
    ordered = sorted(best.values(), key=lambda t: int(t.get("value", 0)), reverse=True)
    return ordered[:MAX_DESTINATIONS]


def trace_fund_flow(wallet: str) -> list[dict]:
    """Main tracing logic: detect outbound transfers and follow the money."""
    import os

    _transfer_cache.clear()
    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    print(f"[tracer] Checking fund flows for {wallet}")
    print(f"[tracer] Etherscan API key: {'configured' if api_key else 'MISSING!'}")

    stored = trace_outbound_transfers(wallet)
    # The novelty filter, applied before unique_destinations so its
    # value-ordered cap ranks only what has not been traced yet.
    outbound = untraced_outbound(wallet, stored)
    findings = []

    if not outbound:
        print("[tracer] No new outbound transfers detected since the last run.")
        latest_path = DATA_DIR / "fund_flows" / "latest.json"
        if not latest_path.exists():
            save_latest(str(DATA_DIR / "fund_flows"), {
                "last_updated": utc_now(),
                "findings": [],
                "status": "NO_NEW_OUTBOUND_TRANSFERS",
            })
        return []

    destinations = unique_destinations(outbound, wallet)
    print(f"[tracer] {len(outbound)} outbound transfers -> {len(destinations)} unique funded destination(s) to trace")

    deadline = time.monotonic() + TRACE_BUDGET_SECONDS
    traced_dests: set[str] = set()
    for i, transfer in enumerate(destinations):
        if time.monotonic() > deadline:
            print(f"[tracer] Time budget ({TRACE_BUDGET_SECONDS}s) reached after {i} destination(s); "
                  f"saving partial results and stopping.")
            break
        destination = transfer["to"]
        traced_dests.add(destination.lower())
        value_raw = int(transfer.get("value", 0))
        # This is a USD dollar figure — _as_etherscan_row encodes amount_usd here,
        # not a token quantity — regardless of what `asset` turns out to be. It
        # only reads correctly as a bare number today because every asset that
        # can reach this path is a STABLES member priced at par (see
        # src/chain/assets.py), where quantity and dollar value coincide. The day
        # a price_lookup for MAJORS is wired (tracked separately), that
        # coincidence ends, so the dollar sign is made explicit here rather than
        # left to hold only by accident of which assets happen to be priced.
        value_usd = value_raw / 1e6
        tx_hash = transfer.get("hash", "unknown")
        # The substrate spans every asset and chain now, so both must come from the
        # row instead of being assumed — before this task every row here WAS
        # Arbitrum USDC by construction (get_usdc_transfers filtered on that one
        # contract), which is the only reason hardcoding either used to be correct.
        asset = transfer.get("tokenSymbol") or "USDC"
        chain = transfer.get("chain") or CHAIN_DEFAULT
        amount_display = f"${value_usd:,.2f}"

        print(f"[tracer] OUTBOUND: {amount_display} of {asset} on {chain} -> {destination}")

        alert_fund_movement(wallet, amount_display, destination, tx_hash,
                            asset=asset, chain=chain)

        print(f"[tracer] Checking if {destination} deposited to Hyperliquid...")
        direct_deposits = find_hl_deposits(destination)
        if direct_deposits:
            print(f"[tracer] !!! NEW WALLET FOUND: {destination} deposited to HL !!!")
            alert_new_wallet_found(wallet, destination, "fund_trace", 1.0)

            findings.append(build_finding(
                wallet,
                destination,
                value_usd,
                tx_hash,
                "direct_fund_trace",
                1,
                True,
                direct_deposits[0].get("hash"),
                asset=asset,
                chain=chain,
            ))
        else:
            print("[tracer] Destination hasn't deposited to HL. Checking next hop...")
            pending_recorded = False
            # get_usdc_transfers is still Arbitrum-USDC-only (see its own docstring),
            # so every build_finding call below sourced from `nt`/`nt2` is genuinely
            # USDC on arbitrum and relies on build_finding's defaults rather than
            # threading asset/chain explicitly.
            next_transfers = get_usdc_transfers(destination)
            for nt in next_transfers[:5]:
                next_dest = nt["to"]
                if next_dest.lower() != destination.lower():
                    next_deposits = find_hl_deposits(next_dest)
                    if next_deposits:
                        print(f"[tracer] !!! NEW WALLET FOUND (2-hop): {next_dest} !!!")
                        alert_new_wallet_found(wallet, next_dest, "fund_trace_2hop", 0.9)
                        next_value_usdc = int(nt.get("value", 0)) / 1e6
                        findings.append(build_finding(
                            wallet,
                            next_dest,
                            next_value_usdc,
                            nt.get("hash", tx_hash),
                            "fund_trace_2hop",
                            2,
                            True,
                            next_deposits[0].get("hash"),
                        ))
                        pending_recorded = True
            if not pending_recorded:
                # Hop 3: follow significant transfers from hop-2 destinations
                for nt in next_transfers[:3]:
                    next_dest = nt["to"]
                    if next_dest.lower() == destination.lower():
                        continue
                    next2_value = int(nt.get("value", 0)) / 1e6
                    if next2_value < 10_000:
                        continue
                    hop3_transfers = get_usdc_transfers(next_dest)
                    for nt2 in hop3_transfers[:3]:
                        final_dest = nt2["to"]
                        if final_dest.lower() == next_dest.lower():
                            continue
                        final_value = int(nt2.get("value", 0)) / 1e6
                        if final_value < 10_000:
                            continue
                        final_deposits = find_hl_deposits(final_dest)
                        if final_deposits:
                            print(f"[tracer] !!! NEW WALLET FOUND (3-hop): {final_dest} !!!")
                            alert_new_wallet_found(wallet, final_dest, "fund_trace_3hop", 0.8)
                            findings.append(build_finding(
                                wallet, final_dest, final_value,
                                nt2.get("hash", tx_hash),
                                "fund_trace_3hop", 3,
                                True, final_deposits[0].get("hash"),
                            ))
                            pending_recorded = True

            if not pending_recorded:
                findings.append(build_finding(
                    wallet,
                    destination,
                    value_usd,
                    tx_hash,
                    "outbound_transfer",
                    1,
                    False,
                    asset=asset,
                    chain=chain,
                ))

    # Advance the marker over what was ACTUALLY processed, and nothing else. A
    # destination the cap or the time budget deferred stays unmarked so the next
    # run picks it up; rows that can never be traced at all (zero value, self
    # transfer) are marked, or the wallet would look permanently dirty and
    # "no new outbound transfers" would never print again.
    deferred = {t["to"].lower() for t in outbound
                if is_traceable(t, wallet)} - traced_dests
    if deferred:
        print(f"[tracer] {len(deferred)} destination(s) deferred to the next run "
              f"(per-run cap {MAX_DESTINATIONS} / time budget); not marked as traced.")
    mark_traced(
        wallet,
        [t.get("record_id") for t in outbound if (t.get("to") or "").lower() not in deferred],
        known_ids={t.get("record_id") for t in stored if t.get("record_id")},
    )

    save_fund_flow_findings(findings)
    _crossref_findings_with_candidates(findings)
    return findings


def _crossref_findings_with_candidates(findings: list[dict],
                                       eff: dict | None = None) -> None:
    """If a fund-flow destination is also a behavioral candidate, fire the combined alert.

    This is the same rule scanner.py applies to a `deposited_to_hl` candidate, and
    it now shares scanner's decision function. Independently, this route used to:

      * skip the style-veto check entirely, so a wallet the scorer had rejected as
        behaviourally incompatible could still be emailed as a CRITICAL match —
        the "sideways to the inbox" hole thresholds.can_alert() documents as
        closed, and the README asserts as an invariant;
      * gate on a hardcoded 0.65 that matched no tier boundary;
      * quote `best_score`, an all-time high-water mark, in an email asserting the
        wallet "matches the behavioral fingerprint" — live data had a candidate at
        best 0.7113 whose current score was 0.1322.
    """
    import json as _json

    hl_findings = [f for f in findings if f.get("deposited_to_hl")]
    if not hl_findings:
        return

    candidates_path = DATA_DIR / "candidates" / "latest.json"
    if not candidates_path.exists():
        return

    try:
        with open(candidates_path) as f:
            data = _json.load(f)
        candidates = {c["wallet"].lower(): c for c in data.get("candidates", [])}
    except Exception:
        return

    if eff is None:
        report = th.load_backtest_report(DATA_DIR.parent / "profile")
        eff = th.resolve(load_config()["alert_thresholds"], report)

    for finding in hl_findings:
        dest = finding.get("destination", "").lower()
        if dest not in candidates:
            continue
        c = candidates[dest]
        score = candidate_current_score(c)
        vetoes = (c.get("latest_evidence") or {}).get("vetoes")
        if not th.combined_alert_ok(score, eff, vetoes, route="deposited_to_hl"):
            if vetoes:
                print(f"[tracer] combined alert suppressed for {dest} "
                      f"(style veto: {'; '.join(vetoes)}); evidence retained")
            else:
                print(f"[tracer] {dest} is a fund-flow destination but scores "
                      f"{score:.4f}, below the combined-alert threshold "
                      f"{th.behavioural_gate(eff):.4f} — no alert")
            continue
        print(f"[tracer] COMBINED SIGNAL: {dest} is both a fund-flow destination "
              f"and behavioral candidate (score={score:.2f})")
        alert_combined_match(
            dest, score,
            finding.get("amount_usdc", "unknown"),
            finding.get("method", "fund_trace"),
        )


def main():
    config = load_config()
    findings = trace_fund_flow(config["target_wallet"])
    print(f"[tracer] Trace complete. Findings: {len(findings)}")

    # Deposit/withdrawal correlation — re-link the target to a fresh wallet across a
    # CEX/cross-chain gap by matching exit amounts to new bridge deposits. Uses the
    # same Etherscan budget as tracing, so it belongs in this job.
    try:
        from src.correlator import run_correlation
        corr = run_correlation()
        print(f"[tracer] Correlation complete. Matches: {corr.get('match_count', 0)}")
    except Exception as e:
        print(f"[tracer] Correlation step failed: {e}")


if __name__ == "__main__":
    main()
