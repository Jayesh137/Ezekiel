# src/boundary/bridge2.py
"""Hyperliquid's Arbitrum bridge, read from its own events.

`FinalizedWithdrawal(address indexed user, address destination, uint64 usd,
uint64 nonce, bytes32 message)` is emitted in the transaction that pays a
withdrawal out, and names the HYPERLIQUID ACCOUNT that withdrew and where the
money went (verified 2026-10-06 on the treasury payout 0xc0758212…). The
Arbitrum logs are therefore a complete, permanent, keyless record of every
Bridge2 withdrawal by every account: ~3,200 a day, a quarter of them to an
address other than the account. On 2026-09-29 this was deferred as needing an
always-on host; it needs one getLogs call per ~6 hours of chain.

`user` is indexed, so one call filtered on an account returns its whole
withdrawal history, and the HL ledger's `withdraw` row carries the same nonce.
Deposits are plain USDC transfers into the bridge: the depositor IS the account
credited.
"""

from __future__ import annotations

from src.boundary.logs import to_int

BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
CHAIN = "arbitrum"
TOPIC_FINALIZED_WITHDRAWAL = "0xe5c7fe3a4ffca1590f26d74c8ba8b0db69557f7f4607a2a43f82e93041611978"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USD_DECIMALS = 6


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + (address or "").lower().removeprefix("0x")


def _word_address(word: str) -> str:
    return "0x" + word[-40:].lower()


def _words(data) -> list[str]:
    h = str(data or "").lower().removeprefix("0x")
    return [h[i:i + 64] for i in range(0, len(h) - len(h) % 64, 64)]


def _common(log: dict) -> dict:
    return {"tx_hash": (log.get("transactionHash") or "").lower(),
            "log_index": to_int(log.get("logIndex")), "block": to_int(log.get("blockNumber")),
            "ts": to_int(log.get("timeStamp"))}


def decode_withdrawal(log: dict) -> dict | None:
    topics = [str(t or "").lower() for t in (log.get("topics") or [])]
    if (len(topics) < 2 or topics[0] != TOPIC_FINALIZED_WITHDRAWAL
            or (log.get("address") or "").lower() != BRIDGE):
        return None
    words = _words(log.get("data"))
    if len(words) < 4:
        return None
    return {"kind": "bridge2_withdrawal", "user": _word_address(topics[1]),
            "destination": _word_address(words[0]),
            "usd": int(words[1], 16) / 10 ** USD_DECIMALS, "nonce": int(words[2], 16),
            "message": "0x" + words[3], **_common(log)}


def decode_deposit(log: dict) -> dict | None:
    topics = [str(t or "").lower() for t in (log.get("topics") or [])]
    if (len(topics) < 3 or topics[0] != TOPIC_TRANSFER
            or (log.get("address") or "").lower() != USDC
            or _word_address(topics[2]) != BRIDGE):
        return None
    words = _words(log.get("data"))
    if not words:
        return None
    return {"kind": "bridge2_deposit", "depositor": _word_address(topics[1]),
            "usd": int(words[0], 16) / 10 ** USD_DECIMALS, **_common(log)}


def withdrawal_topics(user: str | None = None) -> dict:
    topics = {0: TOPIC_FINALIZED_WITHDRAWAL}
    if user:
        topics[1] = topic_address(user)
    return topics


def deposit_topics() -> dict:
    return {0: TOPIC_TRANSFER, 2: topic_address(BRIDGE)}


def event_id(row: dict) -> str:
    return f"{CHAIN}:{row.get('tx_hash')}:{row.get('log_index')}"
