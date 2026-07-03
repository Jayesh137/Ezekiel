# src/tracer.py
"""Traces fund flows on Arbitrum L1 to detect wallet migrations."""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, etherscan_get, read_cursor, write_cursor,
    append_records, save_latest, DATA_DIR
)
from src.alerts import alert_fund_movement, alert_new_wallet_found, alert_combined_match


# Max unique destinations to trace per run — a safety net so a wallet spammed
# with transfers to many addresses can never blow the job timeout.
MAX_DESTINATIONS = 50

# Wall-clock budget for the tracing loop. The CI job has a 5-minute hard
# timeout; stop tracing new destinations after this so partial findings still
# get saved and committed instead of the job being cancelled.
TRACE_BUDGET_SECONDS = 240

# Run-scoped cache of Etherscan transfer lookups, keyed by (address, start_block).
# The same address gets looked up repeatedly (find_hl_deposits + next-hop), so
# caching avoids redundant rate-limited API calls. Cleared at the start of a run.
_transfer_cache: dict[tuple[str, int], list[dict]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Find USDC transfers OUT from the tracked wallet. Returns new transfers."""
    last_block = read_cursor("last_l1_block")
    transfers = get_usdc_transfers(wallet, start_block=last_block)

    if not transfers:
        return []

    outbound = [
        t for t in transfers
        if t.get("from", "").lower() == wallet.lower()
    ]

    append_records(str(DATA_DIR / "l1_transactions"), transfers, key_field="hash")

    if transfers:
        max_block = max(int(t.get("blockNumber", 0)) for t in transfers)
        write_cursor("last_l1_block", max_block)

    return outbound


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
                  bridge_tx_hash: str | None = None) -> dict:
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
    }


def value_to_display(value: float) -> str:
    return f"{value:,.2f}"


def unique_destinations(outbound: list[dict], wallet: str) -> list[dict]:
    """Collapse outbound transfers to one representative per destination.

    Drops zero-value transfers (address-poisoning dust moves no funds) and
    self-transfers, and keeps only the highest-value transfer per destination so
    each destination is traced exactly once. Without this, a wallet spammed with
    hundreds of 0-USDC transfers to the same address triggers hundreds of
    identical Etherscan/SMTP round trips and blows the job timeout.
    """
    best: dict[str, dict] = {}
    for t in outbound:
        dest = t.get("to", "")
        if not dest or dest.lower() == wallet.lower():
            continue
        if int(t.get("value", 0)) <= 0:
            continue
        key = dest.lower()
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

    outbound = trace_outbound_transfers(wallet)
    findings = []

    if not outbound:
        print("[tracer] No new outbound transfers detected. Wallet has not moved USDC on L1.")
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
    for i, transfer in enumerate(destinations):
        if time.monotonic() > deadline:
            print(f"[tracer] Time budget ({TRACE_BUDGET_SECONDS}s) reached after {i} destination(s); "
                  f"saving partial results and stopping.")
            break
        destination = transfer["to"]
        value_raw = int(transfer.get("value", 0))
        value_usdc = value_raw / 1e6  # USDC has 6 decimals
        tx_hash = transfer.get("hash", "unknown")

        print(f"[tracer] OUTBOUND: {value_usdc:.2f} USDC -> {destination}")

        alert_fund_movement(wallet, f"{value_usdc:,.2f}", destination, tx_hash)

        print(f"[tracer] Checking if {destination} deposited to Hyperliquid...")
        direct_deposits = find_hl_deposits(destination)
        if direct_deposits:
            print(f"[tracer] !!! NEW WALLET FOUND: {destination} deposited to HL !!!")
            alert_new_wallet_found(wallet, destination, "fund_trace", 1.0)

            findings.append(build_finding(
                wallet,
                destination,
                value_usdc,
                tx_hash,
                "direct_fund_trace",
                1,
                True,
                direct_deposits[0].get("hash"),
            ))
        else:
            print(f"[tracer] Destination hasn't deposited to HL. Checking next hop...")
            pending_recorded = False
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
                    value_usdc,
                    tx_hash,
                    "outbound_transfer",
                    1,
                    False,
                ))

    save_fund_flow_findings(findings)
    _crossref_findings_with_candidates(findings)
    return findings


def _crossref_findings_with_candidates(findings: list[dict]) -> None:
    """If a fund-flow destination is also a behavioral candidate, fire the combined alert."""
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

    for finding in hl_findings:
        dest = finding.get("destination", "").lower()
        if dest in candidates:
            c = candidates[dest]
            score = float(c.get("best_score", 0))
            if score >= 0.65:
                print(f"[tracer] COMBINED SIGNAL: {dest} is both a fund-flow destination and behavioral candidate (score={score:.2f})")
                alert_combined_match(
                    dest, score,
                    finding.get("amount_usdc", "unknown"),
                    finding.get("method", "fund_trace"),
                )


def main():
    config = load_config()
    findings = trace_fund_flow(config["target_wallet"])
    print(f"[tracer] Trace complete. Findings: {len(findings)}")


if __name__ == "__main__":
    main()
