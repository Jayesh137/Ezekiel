# src/not_wallets.py
"""Addresses that cannot be anyone's trading account. Pure.

A Hyperliquid account is driven by a signature. The zero address, a precompile,
one of Hyperliquid's own system addresses and a token contract have no key, so
nothing they "do" is a trader's choice, and none can ever be the wallet the
owner follows. They still turn up wherever the pipeline takes a transfer's
other side for a counterparty, and every slot they take is a slot a real
suspect does not get. Measured 2026-10-08: two of the forty per-wallet detector
slots (`roster.detector_candidates`) went to USDC on Base (`0x833589fc...`, 65
funding-route observations) and USDC.e on Polygon (`0x2791bca1...`), and the
behavioural scanner was scoring the zero address.

Every contract in CANONICAL_TOKENS was confirmed as that token on 2026-10-08
(rule 3: verify a "well-known" address): Ethereum and Optimism through
Blockscout's token API; Arbitrum, Base, Polygon and HyperEVM (WHYPE) by reading
`symbol()` and `name()` on-chain (Blockscout answered 403 for the first three).
Two existing test fixtures had used 0x2222...2222 and 0x5555...5555 as ordinary
wallets; they are HYPE's system address and WHYPE. The pricing
registry (`data/labels/token_contracts.json`) is read too when a caller passes
it, so a contract added there is excluded here as well.

Ground truth is immune: the target and `known_self_wallets` are never
classified, whatever list they might appear on (the rule
`spam.ground_truth_addresses` already follows).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ADDRESS = re.compile(r"0x[0-9a-f]{40}")
ZERO = "0x" + "0" * 40
ALL_ONES = "0x" + "f" * 40
HYPE_SYSTEM = "0x" + "2" * 40
# At or below this an address is a precompile or a reserved system slot on every
# EVM chain the pipeline reads: Ethereum 0x01-0x0a, HyperEVM's 0x800 range,
# Polygon's 0x1010 native-token contract.
LOW_ADDRESS_LIMIT = 0xFFFF

# Lowercase address -> what it is. The chain is noted for the reader only: a
# contract address has no key on ANY chain, Hyperliquid included.
CANONICAL_TOKENS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC (Ethereum)",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT (Ethereum)",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH (Ethereum)",
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831": "USDC (Arbitrum)",
    "0xff970a61a04b1ca14834a43f5de4533ebddb5cc8": "USDC.e (Arbitrum)",
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": "USDT0 (Arbitrum)",
    "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": "WETH (Arbitrum)",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC (Base)",
    "0x4200000000000000000000000000000000000006": "WETH (Base, Optimism)",
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85": "USDC (Optimism)",
    "0x7f5c764cbc14f9669b88837ca1490cca17c31607": "USDC.e (Optimism)",
    "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": "USDT (Optimism)",
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC (Polygon)",
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e (Polygon)",
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT0 (Polygon)",
    "0x5555555555555555555555555555555555555555": "WHYPE (HyperEVM)",
}


def _norm(address) -> str:
    return str(address or "").strip().lower()


def token_registry_addresses(path: Path | str | None = None) -> set[str]:
    """Every `contract` in the pricing registry, lowercased.

    Read from the `tokens` rows only, never from free text: a comment there may
    name a wallet. An unreadable registry contributes nothing (the canonical list
    still applies)."""
    if path is None:
        from src import utils
        path = utils.DATA_DIR / "labels" / "token_contracts.json"
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    rows = doc.get("tokens") if isinstance(doc, dict) else None
    out = set()
    for row in rows if isinstance(rows, list) else []:
        contract = _norm(row.get("contract")) if isinstance(row, dict) else ""
        if ADDRESS.fullmatch(contract):
            out.add(contract)
    return out


def _ground_truth(config: dict) -> set[str]:
    out = {_norm(config.get("target_wallet"))}
    out |= {_norm(w) for w in config.get("known_self_wallets") or []}
    return {a for a in out if a}


def classify(address, *, config: dict | None = None,
             token_contracts: set | None = None) -> str | None:
    """Why this address cannot be anyone's trading account, or None if it can."""
    a = _norm(address)
    if not ADDRESS.fullmatch(a):
        return "not an address"
    config = config or {}
    if a in _ground_truth(config):
        return None
    if a == ZERO:
        return "the zero address"
    if int(a, 16) <= LOW_ADDRESS_LIMIT:
        return "a precompile or reserved low address"
    if a == ALL_ONES:
        return "the all-ones address"
    if a == HYPE_SYSTEM:
        return "Hyperliquid's HYPE system address"
    # 0x20, then zeros, then a token index: HyperCore's per-token system address.
    if a[2:4] == "20" and a[4:34] == "0" * 30:
        return "a Hyperliquid token system address"
    if a in CANONICAL_TOKENS:
        return f"token contract: {CANONICAL_TOKENS[a]}"
    if token_contracts and a in token_contracts:
        return "token contract (pricing registry)"
    configured = {_norm(x) for x in (config.get("excluded_addresses") or [])}
    configured |= {_norm(x) for x in (config.get("known_service_addresses") or [])}
    if a in configured:
        return "a configured service or excluded address"
    return None
