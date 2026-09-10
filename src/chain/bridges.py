# src/chain/bridges.py
"""Where a bridge transfer actually went, read from the transaction itself.

A trail that "ends at a bridge" is not a dead end: the destination chain and
the recipient are in the calldata. Blockscout returns it decoded for verified
contracts, with no key. Decoded live on 2026-09-10 for every CCTP transfer the
target ever made (51 of 52):

    CctpExtension -> HyperEVM (domain 19): 18 tx, $66,461,024, hookData
        "cctp-forward" + <the target's own address>  -- his own HL account
    CCTP v1/v2 -> Solana (domain 5):   23 tx, $22,751,990, one recipient
    CCTP v2 -> Ethereum (domain 0):    10 tx, $9,999,999, himself
    SocketGateway (175 tx, $320M):      receiver = himself

So the $66M "to infrastructure" was him depositing into his own Hyperliquid
account through Circle, the Solana address is a cluster wallet nobody knew,
and a future CCTP deposit whose hook names a DIFFERENT Hyperliquid account is
the exact tripwire for a fresh account funded this way.

Pure decoders take the Blockscout transaction document; `decode_transfers`
does the I/O with an injectable fetch and a permanent per-hash cache — a
transaction's calldata never changes.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

# Circle CCTP domain ids -> chain names.
CCTP_DOMAINS = {0: "ethereum", 1: "avalanche", 2: "optimism", 3: "arbitrum",
                5: "solana", 6: "base", 7: "polygon", 10: "unichain", 11: "linea",
                12: "codex", 13: "sonic", 14: "worldchain", 16: "sei", 19: "hyperevm"}

# Blockscout hosts per chain, as in src/chain/activity.py.
HOSTS = {
    "arbitrum": "https://arbitrum.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
}

CACHE_NAME = "bridge_decodes.json"

_ADDR = re.compile(r"^[0-9a-f]{40}$")


def _bytes32_to_address(value: str) -> str | None:
    """A bytes32 holding a left-padded EVM address -> 0x address, else None."""
    v = (value or "").lower()
    if v.startswith("0x"):
        v = v[2:]
    if len(v) != 64 or not v.startswith("0" * 24) or not _ADDR.match(v[24:]):
        return None
    addr = v[24:]
    # A word with more than eight zero nibbles at the top of its address part
    # is a number (an amount, a flag), not an address: 0x000000e8d4a51000 is
    # one million USDC, not a wallet.
    if addr.startswith("0" * 8):
        return None
    return "0x" + addr


def _hook_account(hook: str) -> str | None:
    """The account named by the CCTP extension's hook data.

    Observed shape: the ASCII tag "cctp-forward", zero padding, one length
    byte, the 20-byte account, then four zero bytes. The account is the last
    non-zero content, so trailing zero bytes are stripped and the final 20
    bytes taken.
    """
    h = (hook or "").lower()
    if h.startswith("0x"):
        h = h[2:]
    while h.endswith("00"):
        h = h[:-2]
    if len(h) < 40:
        return None
    tail = h[-40:]
    return "0x" + tail if _ADDR.match(tail) else None


def decode_cctp(params: dict) -> dict | None:
    """CCTP depositForBurn (v1 and v2): destination domain and mint recipient."""
    if "destinationDomain" not in params:
        return None
    try:
        domain = int(params["destinationDomain"])
    except (TypeError, ValueError):
        return None
    recip = str(params.get("mintRecipient") or "")
    return {"protocol": "cctp", "domain": domain,
            "chain": CCTP_DOMAINS.get(domain, f"domain-{domain}"),
            "recipient": _bytes32_to_address(recip) or recip.lower(),
            "hl_account": None}


def decode_cctp_extension(params: dict) -> dict | None:
    """Hyperliquid's CctpExtension: batchDepositForBurnWithAuth.

    `_depositForBurnData` is (amount, destinationDomain, mintRecipient,
    destinationCaller, maxFee, minFinalityThreshold, hookData). The mint
    recipient is a forwarder contract on HyperEVM; the account credited on
    HyperCore is in the hook data after the "cctp-forward" tag.
    """
    data = params.get("_depositForBurnData")
    if not isinstance(data, (list, tuple)) or len(data) < 7:
        return None
    try:
        domain = int(data[1])
    except (TypeError, ValueError):
        return None
    hook = str(data[6] or "")
    return {"protocol": "cctp_extension", "domain": domain,
            "chain": CCTP_DOMAINS.get(domain, f"domain-{domain}"),
            "recipient": _bytes32_to_address(str(data[2])) or str(data[2]).lower(),
            "hl_account": _hook_account(hook)}


def decode_socket(raw_input: str, sender: str, token: str | None = None) -> dict | None:
    """SocketGateway routes are not verified for decoding; the receiver is
    the first address-like word after the 4-byte route id and 4-byte selector.
    Observed 2026-09-10 (routes 0x18c and 0x1aa): amount, a small constant,
    receiver, token. Reported as a heuristic."""
    h = (raw_input or "").lower()
    if h.startswith("0x"):
        h = h[2:]
    if len(h) < 16:
        return None
    words = [h[i:i + 64] for i in range(16, len(h) - 63, 64)]
    skip = {(token or "").lower()}
    for w in words:
        a = _bytes32_to_address(w)
        if a and a not in skip:
            return {"protocol": "socket", "domain": None, "chain": None,
                    "recipient": a, "hl_account": None, "heuristic": True}
    return None


def decode_transaction(tx: dict, sender: str, token: str | None = None) -> dict | None:
    """One Blockscout transaction document -> a bridge destination, or None."""
    di = tx.get("decoded_input") if isinstance(tx, dict) else None
    params = {}
    if isinstance(di, dict):
        for p in di.get("parameters") or []:
            if isinstance(p, dict) and p.get("name"):
                params[p["name"]] = p.get("value")
    if "_depositForBurnData" in params:
        return decode_cctp_extension(params)
    if "destinationDomain" in params:
        return decode_cctp(params)
    to_name = ((tx or {}).get("to") or {}).get("name") or ""
    if "socket" in to_name.lower():
        return decode_socket(tx.get("raw_input") or "", sender, token)
    return None


def fetch_transaction(tx_hash: str, chain: str, *, get=None, timeout: float = 45.0) -> dict | None:
    import requests
    host = HOSTS.get((chain or "").lower())
    if not host:
        return None
    get = get or requests.get
    try:
        r = get(f"{host}/api/v2/transactions/{tx_hash}", timeout=timeout,
                headers={"accept": "application/json"})
        if r.status_code != 200:
            return None
        doc = r.json()
        return doc if isinstance(doc, dict) else None
    except Exception:                                 # noqa: BLE001 - transport
        return None


class DecodeCache:
    """tx hash -> decoded destination, permanent. A failed fetch is not cached."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._table = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._table = {}
        if not isinstance(self._table, dict):
            self._table = {}

    def get(self, tx_hash: str):
        return self._table.get((tx_hash or "").lower())

    def put(self, tx_hash: str, value) -> None:
        self._table[(tx_hash or "").lower()] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._table, indent=2, sort_keys=True))


def decode_transfers(records: list[dict], bridge_contracts: set, cluster: set,
                     cache: DecodeCache, *, fetch=None, max_lookups: int = 25,
                     sleep=None) -> tuple[list[dict], int]:
    """Decode every cluster -> bridge record. Returns (rows, lookups spent).

    `records` are substrate rows; one transaction may carry several rows, so
    decoding is per hash. A row whose recipient or Hyperliquid account is not
    a cluster address is marked `foreign`.
    """
    fetch = fetch or fetch_transaction
    cluster = {(c or "").lower() for c in cluster}
    bridges = {(b or "").lower() for b in bridge_contracts}
    spent = 0
    out: list[dict] = []
    seen_hashes: set = set()
    for rec in sorted(records, key=lambda r: -(r.get("ts") or 0)):
        src = (rec.get("src") or "").lower()
        dst = (rec.get("dst") or "").lower()
        h = (rec.get("tx_hash") or "").lower()
        if src not in cluster or dst not in bridges or not h or h in seen_hashes:
            continue
        seen_hashes.add(h)
        decoded = cache.get(h)
        if decoded is None:
            if spent >= max_lookups:
                out.append({"tx_hash": h, "chain": rec.get("chain"), "src": src,
                            "bridge": dst, "amount_usd": rec.get("amount_usd"),
                            "ts": rec.get("ts"), "status": "pending"})
                continue
            spent += 1
            tx = fetch(h, rec.get("chain") or "arbitrum")
            if sleep:
                sleep(0.5)
            if tx is None:
                out.append({"tx_hash": h, "chain": rec.get("chain"), "src": src,
                            "bridge": dst, "amount_usd": rec.get("amount_usd"),
                            "ts": rec.get("ts"), "status": "unreadable"})
                continue
            decoded = decode_transaction(tx, src, rec.get("token_address")) or {"protocol": None}
            cache.put(h, decoded)
        recipient = (decoded.get("recipient") or "") if decoded else ""
        hl_account = (decoded.get("hl_account") or "") if decoded else ""
        landing = hl_account or recipient
        foreign = bool(landing) and landing not in cluster and not (
            decoded.get("protocol") == "cctp_extension" and not hl_account)
        out.append({"tx_hash": h, "chain": rec.get("chain"), "src": src, "bridge": dst,
                    "amount_usd": rec.get("amount_usd"), "ts": rec.get("ts"),
                    "status": "decoded" if decoded.get("protocol") else "undecoded",
                    "protocol": decoded.get("protocol"),
                    "destination_chain": decoded.get("chain"),
                    "recipient": recipient or None,
                    "hl_account": hl_account or None,
                    "heuristic": bool(decoded.get("heuristic")),
                    "foreign": foreign})
    return out, spent


def summarise(rows: list[dict]) -> dict:
    by_dest: dict[str, dict] = {}
    for r in rows:
        if r.get("status") != "decoded":
            continue
        key = f"{r.get('destination_chain')}:{r.get('hl_account') or r.get('recipient')}"
        d = by_dest.setdefault(key, {"chain": r.get("destination_chain"),
                                     "address": r.get("hl_account") or r.get("recipient"),
                                     "protocols": set(), "transfers": 0, "usd": 0.0,
                                     "foreign": r.get("foreign")})
        d["protocols"].add(r.get("protocol"))
        d["transfers"] += 1
        d["usd"] += float(r.get("amount_usd") or 0)
    dests = [{**d, "protocols": sorted(p for p in d["protocols"] if p), "usd": round(d["usd"], 2)}
             for d in by_dest.values()]
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "transfers": len(rows),
        "decoded": sum(1 for r in rows if r.get("status") == "decoded"),
        "pending": sum(1 for r in rows if r.get("status") == "pending"),
        "unreadable": sum(1 for r in rows if r.get("status") == "unreadable"),
        "undecoded": sum(1 for r in rows if r.get("status") == "undecoded"),
        "foreign": [r for r in rows if r.get("foreign")],
        "destinations": sorted(dests, key=lambda d: -d["usd"]),
    }
