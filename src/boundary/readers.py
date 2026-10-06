# src/boundary/readers.py
"""Keyless Blockscout v2 reads for provenance hops and retro attribution.

Every reader spends from a `Budget` and RAISES on failure (rule 5): a hop that
could not be read is recorded as unreadable by the caller, never as "no
source". Values come from Blockscout's per-CONTRACT exchange rate, so a forged
"USDC" (a different contract) has no rate and stays unvalued (rules 2, 6).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from src.boundary.logs import _http_get, to_int

HOSTS = {
    "arbitrum": "https://arbitrum.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
}
FRESH_MAX_TXS = 50


class ReadError(RuntimeError):
    pass


class NoReader(ReadError):
    """No reader exists for this chain: the same answer next run, so final."""


class Budget:
    """Calls (and optionally seconds) a run may spend on Blockscout."""

    def __init__(self, calls: int, seconds: float | None = None, clock=time.monotonic):
        self.calls, self.seconds, self.clock = int(calls), seconds, clock
        self.started, self.used = clock(), 0

    def can(self, n: int = 1) -> bool:
        if self.seconds is not None and self.clock() - self.started >= self.seconds:
            return False
        return self.used + n <= self.calls

    def spend(self) -> None:
        if not self.can():
            raise ReadError("read budget spent")
        self.used += 1


def _ts(value) -> int:
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())


def _call(url: str, params: dict, budget: Budget, get) -> dict:
    budget.spend()
    try:
        doc = (get or _http_get)(url, params)
    except Exception as exc:  # noqa: BLE001 - reported as unreadable, never empty
        raise ReadError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ReadError(f"unexpected answer {type(doc).__name__}")
    return doc


def _host(chain: str) -> str:
    host = HOSTS.get(chain)
    if not host:
        raise NoReader(f"no keyless reader for {chain}")
    return host


def _transfer(item: dict, chain: str) -> dict:
    token, total, frm = item.get("token") or {}, item.get("total") or {}, item.get("from") or {}
    usd = None
    try:
        decimals = int(total.get("decimals") or token.get("decimals"))
        rate = token.get("exchange_rate")
        if rate is not None:
            usd = int(total.get("value")) / 10 ** decimals * float(rate)
    except (TypeError, ValueError):
        usd = None
    return {"from": (frm.get("hash") or "").lower(), "from_is_contract": bool(frm.get("is_contract")),
            "from_is_scam": bool(frm.get("is_scam")), "usd": usd,
            "token": (token.get("address_hash") or "").lower(), "symbol": token.get("symbol"),
            "ts": _ts(item["timestamp"]), "tx_hash": (item.get("transaction_hash") or "").lower(),
            "chain": chain}


def _keyed(has_key) -> bool:
    return bool(os.environ.get("ETHERSCAN_API_KEY")) if has_key is None else bool(has_key)


def _etherscan(params: dict, chain: str, budget: Budget, get) -> dict:
    """One Etherscan V2 call; RAISES on anything but an answer (rule 5).

    With a key it is read before Blockscout: Blockscout's v2 API answered 403
    to the GitHub runner on the first live run (2026-10-06), and its index has
    holes (src/boundary/logs.py). A refusal - a plan that does not serve the
    chain, a rate limit - raises, and the caller falls back to Blockscout.
    """
    from src.boundary.logs import ETHERSCAN_CHAIN_IDS
    if chain not in ETHERSCAN_CHAIN_IDS:
        raise NoReader(f"no Etherscan reader for {chain}")
    budget.spend()
    if get is None:
        from src.utils import etherscan_get as get
    doc = get(params, chain_id=ETHERSCAN_CHAIN_IDS[chain])
    if not isinstance(doc, dict):
        raise ReadError(f"etherscan: unexpected answer {type(doc).__name__}")
    return doc


def _etherscan_rows(doc: dict) -> list[dict]:
    if str(doc.get("status")) == "1" and isinstance(doc.get("result"), list):
        return doc["result"]
    if "no transactions found" in str(doc.get("message") or "").lower():
        return []
    raise ReadError(f"etherscan: {doc.get('message')} {str(doc.get('result'))[:120]}")


_VALUING: dict = {}


def _value(symbol, amount: float, ts: int, contract: str, chain: str):
    """By contract, the project's own rule (chain/assets.value_usd): canonical
    stables and registry par tokens; impostors and everything else unvalued."""
    from src.chain.assets import load_canonical_contracts, load_par_contracts, value_usd
    if not _VALUING:
        from src.utils import DATA_DIR, load_config
        registry = DATA_DIR / "labels" / "token_contracts.json"
        _VALUING.update(canonical=load_canonical_contracts(load_config(), registry),
                        par=load_par_contracts(registry))
    day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
    usd, _basis = value_usd(symbol or "", amount, day, lambda *a: None, contract=contract,
                            chain=chain, canonical=_VALUING["canonical"],
                            par_contracts=_VALUING["par"])
    return usd


def _etherscan_inbound(chain, address, since_ts, until_ts, budget, get) -> list[dict]:
    address = (address or "").lower()
    rows = _etherscan_rows(_etherscan({"module": "account", "action": "tokentx",
                                       "address": address, "startblock": 0,
                                       "endblock": 99_999_999_999, "page": 1, "offset": 1_000,
                                       "sort": "desc"}, chain, budget, get))
    out = []
    for r in rows:
        try:
            ts = int(r.get("timeStamp") or 0)
            amount = int(r.get("value") or 0) / 10 ** int(r.get("tokenDecimal") or 0)
        except (TypeError, ValueError):
            continue
        if (r.get("to") or "").lower() != address or ts > until_ts:
            continue
        if ts < since_ts:
            break                          # newest first: the rest is older
        token = (r.get("contractAddress") or "").lower()
        out.append({"from": (r.get("from") or "").lower(), "from_is_contract": False,
                    "from_is_scam": False, "token": token, "symbol": r.get("tokenSymbol"),
                    "usd": _value(r.get("tokenSymbol"), amount, ts, token, chain), "ts": ts,
                    "tx_hash": (r.get("hash") or "").lower(), "chain": chain})
    return out


def inbound_transfers(chain: str, address: str, *, since_ts: int, until_ts: int, budget: Budget,
                      get=None, pages: int = 2, has_key=None, etherscan=None) -> list[dict]:
    """ERC-20 transfers INTO `address` with since_ts <= ts <= until_ts, newest first."""
    if _keyed(has_key):
        try:
            return _etherscan_inbound(chain, address, since_ts, until_ts, budget, etherscan)
        except ReadError:
            pass                           # Blockscout, keyless, below
    url = f"{_host(chain)}/api/v2/addresses/{address}/token-transfers"
    params = {"type": "ERC-20", "filter": "to"}
    out = []
    for _ in range(max(1, pages)):
        doc = _call(url, params, budget, get)
        items = doc.get("items")
        if not isinstance(items, list):
            raise ReadError("invalid Blockscout page")
        older = False
        for item in items:
            try:
                row = _transfer(item, chain)
            except (KeyError, TypeError, ValueError):
                continue
            if row["ts"] > until_ts:
                continue
            if row["ts"] < since_ts:
                older = True
                break
            out.append(row)
        nxt = doc.get("next_page_params")
        if older or not isinstance(nxt, dict) or not nxt:
            break
        params = {"type": "ERC-20", "filter": "to", **nxt}
    return out


def _etherscan_first_gas(chain, address, budget, get, fresh_max) -> dict | None:
    address = (address or "").lower()
    rows = _etherscan_rows(_etherscan({"module": "account", "action": "txlist",
                                       "address": address, "startblock": 0,
                                       "endblock": 99_999_999_999, "page": 1,
                                       "offset": fresh_max + 1, "sort": "asc"}, chain, budget, get))
    if len(rows) > fresh_max:
        return None                        # an established address
    for r in rows:
        if ((r.get("to") or "").lower() == address and str(r.get("isError")) != "1"
                and str(r.get("value") or "0") not in ("0", "")):
            return {"from": (r.get("from") or "").lower(), "ts": int(r.get("timeStamp") or 0),
                    "tx_hash": (r.get("hash") or "").lower(), "fresh": True}
    return None


def first_gas(chain: str, address: str, *, budget: Budget, get=None,
              fresh_max: int = FRESH_MAX_TXS, has_key=None, etherscan=None) -> dict | None:
    """Who paid a FRESH address its first native gas; None for an established one."""
    if _keyed(has_key):
        try:
            return _etherscan_first_gas(chain, address, budget, etherscan, fresh_max)
        except ReadError:
            pass
    host = _host(chain)
    counters = _call(f"{host}/api/v2/addresses/{address}/counters", {}, budget, get)
    try:
        txs = int(counters.get("transactions_count") or 0)
    except (TypeError, ValueError):
        return None
    if txs > fresh_max:
        return None
    doc = _call(f"{host}/api/v2/addresses/{address}/transactions", {"filter": "to"}, budget, get)
    rows = [i for i in doc.get("items") or [] if isinstance(i, dict)
            and str(i.get("value") or "0") not in ("0", "")
            and (i.get("from") or {}).get("hash")]
    if not rows:
        return None
    oldest = min(rows, key=lambda i: _ts(i["timestamp"]))
    return {"from": oldest["from"]["hash"].lower(), "ts": _ts(oldest["timestamp"]),
            "tx_hash": (oldest.get("hash") or "").lower(), "fresh": True}


def _etherscan_tx_logs(chain, tx_hash, budget, get) -> list[dict]:
    doc = _etherscan({"module": "proxy", "action": "eth_getTransactionReceipt",
                      "txhash": tx_hash}, chain, budget, get)
    receipt = doc.get("result")
    if not isinstance(receipt, dict) or not isinstance(receipt.get("logs"), list):
        raise ReadError(f"etherscan receipt: {str(doc)[:160]}")
    return [{"address": (x.get("address") or "").lower(), "topics": x.get("topics") or [],
             "data": x.get("data") or "0x", "logIndex": x.get("logIndex") or "0x",
             "transactionHash": (x.get("transactionHash") or tx_hash).lower(),
             "blockNumber": x.get("blockNumber") or "0x", "timeStamp": None}
            for x in receipt["logs"]]


def tx_logs(chain: str, tx_hash: str, *, budget: Budget, get=None, has_key=None,
            etherscan=None) -> list[dict]:
    """A transaction's logs in Etherscan's shape, so the log decoders apply."""
    if _keyed(has_key):
        try:
            return _etherscan_tx_logs(chain, tx_hash, budget, etherscan)
        except ReadError:
            pass
    doc = _call(f"{_host(chain)}/api/v2/transactions/{tx_hash}/logs", {}, budget, get)
    items = doc.get("items")
    if not isinstance(items, list):
        raise ReadError("invalid Blockscout logs page")
    # Etherscan's shape is hex strings: Blockscout v2 answers integers, and a
    # decoder that parses with int(x, 16) (circle_flows) crashes on an int.
    return [{"address": ((i.get("address") or {}).get("hash") or "").lower(),
             "topics": [t for t in (i.get("topics") or []) if t],
             "data": i.get("data") or "0x", "logIndex": hex(to_int(i.get("index"))),
             "transactionHash": (i.get("transaction_hash") or tx_hash).lower(),
             "blockNumber": hex(to_int(i.get("block_number"))), "timeStamp": None}
            for i in items]


def block_of(row: dict) -> int:
    return to_int(row.get("blockNumber"))
