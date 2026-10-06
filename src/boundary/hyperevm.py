# src/boundary/hyperevm.py
"""Where a credit through Circle's deposit wallet came from, read on HyperEVM.

A send from `0x6b9e7731…` on HyperCore is Circle's CoreDepositWallet crediting
an account. The USDC that paid for it moved on HyperEVM in the block the credit
landed in: from Circle's CctpForwarder when it arrived as a Circle message from
another chain — whose `MessageReceived`, in the same transaction, names the
source domain and sender — or from whichever HyperEVM address paid it in.
Measured 2026-10-06: the dry run's "unresolved" $15.3M account had deposited
its own HyperEVM USDC; the Arbitrum-only reader could never have said so.

`call(method, params)` is an EVM JSON-RPC call (the public RPC, or Etherscan's
proxy for chain 999). Anything it raises is an EvmReadError here: a read that
failed is never "no deposit" (rule 5).
"""

from __future__ import annotations

USDC = "0xb88339cb7199b77e23db6e890353e22632ba630f"
DEPOSIT_WALLET = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
CCTP_FORWARDER = "0xb21d281dedb17ae5b501f6aa8256fe38c4e45757"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SECONDS_PER_BLOCK = 0.9837          # measured over 2M blocks, 2026-10-06
SOLANA_DOMAIN = 5
BEFORE, AFTER = 5, 2                # blocks around the credit's own
SEARCH_READS = 4


class EvmReadError(RuntimeError):
    """A HyperEVM read that returned no answer."""


def _call(call, method, params):
    try:
        return call(method, params)
    except Exception as exc:  # noqa: BLE001 - transport or rate limit: unreadable
        raise EvmReadError(f"{method}: {type(exc).__name__}: {exc}") from exc


def _int(value) -> int:
    s = str(value)
    return int(s, 16) if s.startswith("0x") else int(s)


def block_at(ts: int, *, call) -> int:
    """The HyperEVM block stamped `ts`: estimated from the head, then refined."""
    def ts_of(block):
        return _int((_call(call, "eth_getBlockByNumber", [hex(block), False]) or {})["timestamp"])
    head = _int(_call(call, "eth_blockNumber", []))
    block = head - int((ts_of(head) - ts) / SECONDS_PER_BLOCK)
    for _ in range(SEARCH_READS):
        gap = ts - ts_of(block)
        if abs(gap) <= 1:
            return block
        block += round(gap / SECONDS_PER_BLOCK)
    raise EvmReadError(f"no HyperEVM block found for {ts}")


def deposit_source(account: str, usd: float, ts: int, *, call) -> dict | None:
    """The payer behind one deposit-wallet credit, or None when not unique."""
    from src import circle_flows as cf
    from src.chain.bridges import CCTP_DOMAINS

    account = (account or "").lower()
    block = block_at(int(ts), call=call)
    logs = _call(call, "eth_getLogs", [{
        "fromBlock": hex(block - BEFORE), "toBlock": hex(block + AFTER), "address": USDC,
        "topics": [TOPIC_TRANSFER, None, "0x" + "0" * 24 + DEPOSIT_WALLET[2:]]}]) or []
    hits = [x for x in logs
            if abs(_int(x.get("data") or "0x0") / 1e6 - float(usd)) <= max(0.01, float(usd) * 1e-6)]
    if len(hits) != 1:
        return None
    payer = "0x" + hits[0]["topics"][1][-40:].lower()
    if payer != CCTP_FORWARDER:
        return {"address": payer, "chain": "hyperevm", "kind": "hyperevm_payer",
                "tx_hash": (hits[0].get("transactionHash") or "").lower()}
    receipt = _call(call, "eth_getTransactionReceipt", [hits[0]["transactionHash"]]) or {}
    messages = [m for m in (cf.decode_received(x) for x in receipt.get("logs") or []) if m]
    mine = [m for m in messages if m.get("hl_account") == account] or messages
    if len(mine) != 1:
        return None
    m = mine[0]
    solana = m["domain"] == SOLANA_DOMAIN
    return {"address": m["counterparty_raw"] if solana else m["message_sender"],
            "raw": m["counterparty_raw"], "chain": CCTP_DOMAINS.get(m["domain"], m["chain"]),
            "domain": m["domain"], "kind": "circle_message", "message_usd": m["amount_usd"],
            "tx_hash": m["tx_hash"]}
