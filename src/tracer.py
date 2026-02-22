# src/tracer.py
"""Traces fund flows on Arbitrum L1 to detect wallet migrations."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    load_config, etherscan_get, read_cursor, write_cursor,
    append_records, save_latest, DATA_DIR
)
from src.alerts import alert_fund_movement, alert_new_wallet_found


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


def check_if_hl_deposit(address: str) -> bool:
    """Check if an address has deposited to the Hyperliquid bridge."""
    config = load_config()
    transfers = get_usdc_transfers(address)
    bridge = config["hl_bridge_contract"].lower()
    return any(
        t["to"].lower() == bridge for t in transfers
    )


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


def trace_fund_flow(wallet: str) -> None:
    """Main tracing logic: detect outbound transfers and follow the money."""
    import os
    config = load_config()

    api_key = os.environ.get("ETHERSCAN_API_KEY", "")
    print(f"[tracer] Checking fund flows for {wallet}")
    print(f"[tracer] Etherscan API key: {'configured (' + api_key[:6] + '...)' if api_key else 'MISSING!'}")

    outbound = trace_outbound_transfers(wallet)

    if not outbound:
        print("[tracer] No new outbound transfers detected. Wallet has not moved USDC on L1.")
        return

    for transfer in outbound:
        destination = transfer["to"]
        value_raw = int(transfer.get("value", 0))
        value_usdc = value_raw / 1e6  # USDC has 6 decimals
        tx_hash = transfer.get("hash", "unknown")

        print(f"[tracer] OUTBOUND: {value_usdc:.2f} USDC -> {destination}")

        alert_fund_movement(wallet, f"{value_usdc:,.2f}", destination, tx_hash)

        print(f"[tracer] Checking if {destination} deposited to Hyperliquid...")
        if check_if_hl_deposit(destination):
            print(f"[tracer] !!! NEW WALLET FOUND: {destination} deposited to HL !!!")
            alert_new_wallet_found(wallet, destination, "fund_trace", 1.0)

            finding = {
                "source": wallet,
                "destination": destination,
                "amount_usdc": value_usdc,
                "tx_hash": tx_hash,
                "method": "direct_fund_trace",
                "deposited_to_hl": True,
            }
            save_latest(str(DATA_DIR / "scans"), {"fund_trace_findings": [finding]})
        else:
            print(f"[tracer] Destination hasn't deposited to HL. Checking next hop...")
            next_transfers = get_usdc_transfers(destination)
            for nt in next_transfers[:5]:
                next_dest = nt["to"]
                if next_dest.lower() != destination.lower():
                    if check_if_hl_deposit(next_dest):
                        print(f"[tracer] !!! NEW WALLET FOUND (2-hop): {next_dest} !!!")
                        alert_new_wallet_found(wallet, next_dest, "fund_trace_2hop", 0.9)


def main():
    config = load_config()
    trace_fund_flow(config["target_wallet"])
    print("[tracer] Trace complete.")


if __name__ == "__main__":
    main()
