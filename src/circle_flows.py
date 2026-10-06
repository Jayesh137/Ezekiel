# src/circle_flows.py
"""Every Circle transfer into and out of Hyperliquid, with both ends named.

Circle's MessageTransmitterV2 on HyperEVM (`0x81d40f21…`, the same address on
every EVM chain CCTP v2 runs on) emits one event per Circle transfer that
touches Hyperliquid, and the event carries identities the rest of the project
could only infer:

- `MessageReceived` — a deposit INTO Hyperliquid. Its burn message names the
  source domain, the `messageSender` who called the burn on the source chain (an
  EVM address, or a Solana pubkey, as bytes32), the amount, and hook data
  tagged `cctp-forward` naming the HyperCore account credited.
- `MessageSent` — a withdrawal OUT of Hyperliquid (`sendToEvmWithData`). Its
  message names the destination domain, the `mintRecipient` on that chain,
  the amount, and a `messageSender` that is the withdrawing HyperCore account
  (measured 2026-09-17: `userRole` answers "user" for it).

Why this exists: `0xf078969e…`, his, holds $11.8M in Aave on Monad (see
CLAUDE.md). A burn from Monad straight into a NEW Hyperliquid account would be
decoded by nothing — no Blockscout decoder for Monad, and not an exit of the
target's for the amount correlator to match. Read from the Hyperliquid end, the
source chain does not matter: every Circle route in and out is one feed, it is
keyless, and unlike the explorer's 300-action window it cannot roll out.

Two tripwires come out of it, both observed transfers rather than inferences:

- **his wallet funded an account outside the cluster** — `messageSender` is his
  (any chain) and the credited account is not;
- **an account outside the cluster paid one of his addresses** — a withdrawal
  whose recipient is his, from an account that is not: the deposit-address
  sentinel's logic, read from the Hyperliquid side.

Pure: scripts/check_circle_flows.py does the I/O.

`messageSender` can be a router. It is not necessarily the original funder.
The decoded hook names an instructed recipient; credit confirmation needs a
forwarding event or HyperCore ledger observation. Historical indexing can lag.
"""

MESSAGE_TRANSMITTER_V2 = "0x81d40f21f12a8f0e3252bccb954d722d4c464b64"
TOPIC_MESSAGE_RECEIVED = "0xff48c13eda96b1cceacc6b9edeedc9e9db9d6226afbc30146b720c19d3addb1c"
TOPIC_MESSAGE_SENT = "0x8c5261668696ce22758910d05bab8f186d6eb247ceac2af2e82c7dc17669b036"
HYPEREVM_DOMAIN = 19
USDC_DECIMALS = 6

KIND_FUNDED_OUTSIDE = "his_wallet_funded_outside_account"
KIND_OUTSIDE_PAID_HIM = "outside_account_paid_his_address"
KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE = "his_account_withdrew_to_outside_address"
KIND_UNATTRIBUTED_PAID_HIM = "unattributed_withdrawal_paid_his_address"

# USDC's linked contract on HyperEVM. A HyperCore-native Circle withdrawal
# (`sendToEvmWithData`) is burned BY it, so its message names it as sender and
# the account only in the hook (measured 2026-10-06 on his own $6,000,000
# withdrawal of 2026-09-15). A HyperEVM-native burn names its own sender.
FORWARDER = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
# HyperCore's system address for USDC: every account's send into it (Circle
# withdrawals and moves to HyperEVM, ~2,700 a day) is on its own ledger, with
# the exact amount the Circle message carries.
SYSTEM_USDC = "0x2000000000000000000000000000000000000000"
AMOUNT_MATCH_USD = 0.005


def _hex(data: str) -> str:
    h = (data or "").lower()
    return h[2:] if h.startswith("0x") else h


def _word(h: str, index: int) -> str:
    return h[index * 64:(index + 1) * 64]


def _address(word: str) -> str | None:
    """A bytes32 word holding a left-padded EVM address, else None."""
    from src.chain.bridges import _bytes32_to_address
    return _bytes32_to_address(word)


def decode_burn_body(body: str) -> dict | None:
    """BurnMessageV2: version, burnToken, mintRecipient, amount, messageSender,
    maxFee, feeExecuted, expirationBlock, hookData. Hex without 0x."""
    b = _hex(body)
    if len(b) < 8 + 64 * 7:
        return None
    words = b[8:]
    try:
        amount = int(words[64 * 2:64 * 3], 16) / 10 ** USDC_DECIMALS
    except ValueError:
        return None
    return {
        "burn_token": "0x" + words[0:64],
        "mint_recipient_raw": "0x" + words[64:128],
        "mint_recipient": _address(words[64:128]),
        "amount_usd": round(amount, 6),
        "message_sender_raw": "0x" + words[64 * 3:64 * 4],
        "message_sender": _address(words[64 * 3:64 * 4]),
        "hook": words[64 * 7:],
    }


def decode_received(log: dict) -> dict | None:
    """A MessageReceived log -> a deposit INTO Hyperliquid, or None."""
    from src.chain.bridges import CCTP_DOMAINS, _hook_account

    topics = log.get("topics") or []
    if not topics or topics[0].lower() != TOPIC_MESSAGE_RECEIVED:
        return None
    h = _hex(log.get("data"))
    try:
        domain = int(_word(h, 0), 16)
        offset = int(_word(h, 2), 16) * 2
        length = int(h[offset:offset + 64], 16) * 2
    except ValueError:
        return None
    body = decode_burn_body(h[offset + 64:offset + 64 + length])
    if body is None:
        return None
    return {
        "direction": "in", "domain": domain,
        "event_id": f"hyperevm:{log.get('transactionHash')}:{log.get('logIndex', 'received')}",
        "protocol_message_id": f"{domain}:{topics[2].lower()}" if len(topics) > 2 else None,
        "protocol_id_verified": len(topics) > 2 and topics[2].lower() != "0x" + "0" * 64,
        "message_sender": body["message_sender"], "original_funder": None,
        "protocol_sender_raw": "0x" + _word(h, 1),
        "burn_token": body["burn_token"], "mint_recipient": body["mint_recipient"],
        "credit_confirmed": False,
        "chain": CCTP_DOMAINS.get(domain, f"domain-{domain}"),
        "counterparty": body["message_sender"],
        "counterparty_raw": body["message_sender_raw"],
        "hl_account": _hook_account(body["hook"]),
        "hook_account": _hook_account(body["hook"]),
        "amount_usd": body["amount_usd"],
        "tx_hash": (log.get("transactionHash") or "").lower(),
        "block": int(log.get("blockNumber") or "0x0", 16),
    }


def decode_sent(log: dict) -> dict | None:
    """A MessageSent log from HyperEVM -> a withdrawal OUT of Hyperliquid, or None.

    `hl_account` is the message sender; for a forwarder-burned withdrawal that
    is the forwarder, and `apply_withdrawers` must resolve the real account.
    """
    from src.chain.bridges import CCTP_DOMAINS, _hook_account

    topics = log.get("topics") or []
    if not topics or topics[0].lower() != TOPIC_MESSAGE_SENT:
        return None
    h = _hex(log.get("data"))
    try:
        offset = int(_word(h, 0), 16) * 2
        length = int(h[offset:offset + 64], 16) * 2
    except ValueError:
        return None
    message = h[offset + 64:offset + 64 + length]
    # Header: version(4) source(4) destination(4) nonce(32) sender(32)
    # recipient(32) destinationCaller(32) minFinality(4) finalityExecuted(4).
    header = 8 * 3 + 64 * 4 + 8 * 2
    if len(message) < header:
        return None
    try:
        source = int(message[8:16], 16)
        destination = int(message[16:24], 16)
    except ValueError:
        return None
    if source != HYPEREVM_DOMAIN:
        return None
    body = decode_burn_body(message[header:])
    if body is None:
        return None
    return {
        "direction": "out", "domain": destination,
        "event_id": f"hyperevm:{log.get('transactionHash')}:{log.get('logIndex', 'sent')}",
        "protocol_message_id": f"{source}:0x{message[24:88]}" if int(message[24:88], 16) else None,
        "protocol_id_verified": bool(int(message[24:88], 16)),
        "message_sender": body["message_sender"], "original_funder": None,
        "burn_token": body["burn_token"], "mint_recipient": body["mint_recipient"],
        "chain": CCTP_DOMAINS.get(destination, f"domain-{destination}"),
        "counterparty": body["mint_recipient"],
        "counterparty_raw": body["mint_recipient_raw"],
        "hl_account": body["message_sender"],
        "hook_account": _hook_account(body["hook"]),
        "amount_usd": body["amount_usd"],
        "tx_hash": (log.get("transactionHash") or "").lower(),
        "block": int(log.get("blockNumber") or "0x0", 16),
    }


def decode(log: dict) -> dict | None:
    return decode_received(log) or decode_sent(log)


def system_sends(ledger_rows) -> list[dict]:
    """Every account's send into the USDC system address, from its ledger. Pure."""
    out = []
    for row in ledger_rows or []:
        d = (row or {}).get("delta") or {}
        if d.get("type") != "send" or (d.get("destination") or "").lower() != SYSTEM_USDC:
            continue
        user = (d.get("user") or "").lower()
        try:
            amount = float(d.get("amount"))
        except (TypeError, ValueError):
            continue
        if user and user != SYSTEM_USDC:
            out.append({"user": user, "amount": amount, "ts_ms": int(row.get("time") or 0),
                        "hash": row.get("hash")})
    return out


def resolve_withdrawer(row: dict, sends, *, readable: bool = True) -> tuple:
    """Who withdrew: the message sender, unless the forwarder burned it. Pure."""
    if row.get("direction") != "out" or (row.get("message_sender") or "").lower() != FORWARDER:
        return row.get("hl_account"), "message_sender"
    if not readable:
        return None, "unreadable"
    try:
        amount = float(row.get("amount_usd"))
    except (TypeError, ValueError):
        return None, "unresolved"
    users = {s["user"] for s in sends or []
             if abs(float(s["amount"]) - amount) <= AMOUNT_MATCH_USD}
    if len(users) == 1:
        return next(iter(users)), "system_ledger"
    hook = (row.get("hook_account") or "").lower()
    if hook and hook in users:
        return hook, "system_ledger+hook"
    return None, "unresolved"


def apply_withdrawers(rows, sends, *, readable: bool) -> list[dict]:
    """Rows with `hl_account` resolved for every forwarder-burned withdrawal. Pure."""
    out = []
    for row in rows or []:
        account, basis = resolve_withdrawer(row, sends, readable=readable)
        out.append({**row, "hl_account": account, "hl_account_basis": basis})
    return out


def base58_to_hex(address: str) -> str | None:
    """A Solana base58 address -> its 32 bytes as 0x-hex, or None."""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = 0
    for ch in address or "":
        i = alphabet.find(ch)
        if i < 0:
            return None
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    raw = b"\x00" * (len(address) - len(address.lstrip("1"))) + raw
    return "0x" + raw.hex() if len(raw) == 32 else None


def classify(row: dict, his_evm: set, his_raw: set, cluster_accounts: set) -> str | None:
    """Which tripwire, if any, a decoded flow trips. Pure.

    `his_evm`: EVM addresses that are his on other chains (config cluster, roster
    CONFIRMED, private deposit addresses). `his_raw`: 0x-prefixed bytes32 forms of
    non-EVM addresses (his Solana wallet). `cluster_accounts`: the Hyperliquid
    accounts that are his — the config cluster.

    A forwarder-burned withdrawal must have had its account resolved first
    (`apply_withdrawers`): its message sender is the forwarder, never the
    account. One resolved to nobody that paid his address is its own kind; one
    whose ledger could not be read is not judged here (it is retried).
    """
    theirs_is_his = ((row.get("counterparty") or "").lower() in his_evm
                     or (row.get("counterparty_raw") or "").lower() in his_raw)
    account = (row.get("hl_account") or "").lower()
    if not account:
        if (row.get("direction") == "out" and theirs_is_his
                and row.get("hl_account_basis") == "unresolved"):
            return KIND_UNATTRIBUTED_PAID_HIM
        return None
    account_is_his = account in cluster_accounts
    if row.get("direction") == "in" and theirs_is_his and not account_is_his:
        return KIND_FUNDED_OUTSIDE
    if row.get("direction") == "out" and theirs_is_his and not account_is_his:
        return KIND_OUTSIDE_PAID_HIM
    if row.get("direction") == "out" and account_is_his and not theirs_is_his             and (row.get("counterparty") or row.get("counterparty_raw")):
        return KIND_HIS_ACCOUNT_WITHDREW_OUTSIDE
    return None


def finding_key(row: dict) -> str:
    return f"{row.get('tx_hash')}:{(row.get('hl_account') or '').lower()}"
