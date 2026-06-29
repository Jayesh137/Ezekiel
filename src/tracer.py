# src/tracer.py
"""Traces fund flows on Arbitrum L1 to detect wallet migrations."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, etherscan_get, read_cursor, write_cursor,
    append_records, save_latest, DATA_DIR
)
from src.alerts import alert_fund_movement, alert_new_wallet_found


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_usdc_transfers(address: str, start_block: int = 0) -> list[dict]:
    """Get all USDC token transfers for an address on Arbitrum."""
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
        return transfers
    elif status == "0" and message == "No transactions found":
        print(f"[tracer] Etherscan: No USDC transfers for {address[:10]}... (confirmed empty)")
        return []
    else:
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


def trace_fund_flow(wallet: str) -> list[dict]:
    """Main tracing logic: detect outbound transfers and follow the money."""
    import os

    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    print(f"[tracer] Checking fund flows for {wallet}")
    print(f"[tracer] Etherscan API key: {'configured (' + api_key[:6] + '...)' if api_key else 'MISSING!'}")

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

    for transfer in outbound:
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
    return findings


def main():
    config = load_config()
    findings = trace_fund_flow(config["target_wallet"])
    print(f"[tracer] Trace complete. Findings: {len(findings)}")


if __name__ == "__main__":
    main()
