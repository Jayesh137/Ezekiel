#!/usr/bin/env python3
"""What the Etherscan key can see on HyperEVM (chain id 999), and what it holds.

The public HyperEVM RPC caps log queries at 1000 blocks against one-second
blocks, so this project watches the chain by nonce and records its history as
unreconstructable. That is true of the PUBLIC RPC and not of the index:
measured 2026-09-11, Etherscan V2 serves chain 999 from the same key as every
other chain, `status: "1"`, real rows.

This matters because of a standing hole. The target has sent $30,000,000 of
spot USDC to `0x2000...0000` — the HyperCore system address for token index 0
— across six transfers between 2026-06-12 and 2026-09-11, while his HyperEVM
nonce has stayed 0. Nothing has come back that way, the linked USDC contract
reverts on `balanceOf` and `symbol`, and a targeted `eth_getLogs` window
around the 2026-09-11 transfer found no Transfer event crediting his address.
So where that money sits has never been observed, only assumed.

This is a SURVEY, not a collector. It reports what the index returns —
contracts, symbols, counts, whether he has ever sent anything — and writes it
down. Enabling chain 999 in `config.chains` would feed the substrate, and the
substrate prices a transfer by its symbol: a token calling itself USDC on a
chain with no canonical contract on record would be booked at par, which is
the counterfeit bug that once put $3.07B of fake money in this system. So the
chain is surveyed first and enabled only once its real USDC contract is known.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.assets import STABLES
from src.utils import DATA_DIR, etherscan_get, load_config, save_latest

PROBE_DIR = DATA_DIR / "hyperevm" / "etherscan_probe"
CHAIN_ID = 999
# Rows per kind. Enough to characterise what is there without pretending to
# be a sweep; the substrate is what would collect properly.
PAGE = 200


def _rows(action: str, address: str) -> tuple[list, str]:
    payload = etherscan_get({"module": "account", "action": action,
                             "address": address, "page": 1, "offset": PAGE,
                             "sort": "desc"}, chain_id=CHAIN_ID)
    result = payload.get("result")
    if payload.get("status") == "1" and isinstance(result, list):
        return result, "ok"
    message = str(payload.get("message") or "")
    if message.lower().startswith("no "):
        return [], "empty"
    return [], f"unsupported: {result or message}"


def survey(address: str) -> dict:
    """Every token that has touched this address on HyperEVM, by contract."""
    out = {"address": address.lower(), "kinds": {}, "tokens": {}, "sent_any": None}

    erc20, status = _rows("tokentx", address)
    out["kinds"]["erc20"] = {"status": status, "rows": len(erc20)}
    for row in erc20:
        contract = (row.get("contractAddress") or "").lower()
        symbol = row.get("tokenSymbol") or ""
        key = f"{symbol}@{contract}"
        t = out["tokens"].setdefault(key, {
            "symbol": symbol, "contract": contract, "in": 0, "out": 0,
            "first_ts": None, "last_ts": None,
            # The whole reason to survey before enabling the chain: a token
            # wearing a stablecoin's ticker would be priced at par.
            "would_be_priced_at_par": symbol.strip().upper() in STABLES,
        })
        if (row.get("to") or "").lower() == address.lower():
            t["in"] += 1
        else:
            t["out"] += 1
        try:
            ts = int(row.get("timeStamp") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts:
            t["first_ts"] = min(t["first_ts"] or ts, ts)
            t["last_ts"] = max(t["last_ts"] or 0, ts)

    native, status = _rows("txlist", address)
    out["kinds"]["native"] = {"status": status, "rows": len(native)}
    sent = [r for r in native if (r.get("from") or "").lower() == address.lower()]
    out["sent_any"] = bool(sent) if status in ("ok", "empty") else None

    internal, status = _rows("txlistinternal", address)
    out["kinds"]["internal"] = {"status": status, "rows": len(internal)}
    return out


def main() -> int:
    config = load_config()
    wallets = [(config["target_wallet"].lower(), "the target")]
    wallets += [((w or "").lower(), "a known wallet") for w in
                config.get("known_self_wallets", []) or []]

    out = {"checked_at": datetime.now(UTC).isoformat(), "chain_id": CHAIN_ID,
           "key_present": bool(os.environ.get("ETHERSCAN_API_KEY")), "wallets": {}}
    if not out["key_present"]:
        out["result"] = "no key — untested"
        save_latest(str(PROBE_DIR), out)
        print("[hyperevm-index] no ETHERSCAN_API_KEY — chain 999 untested")
        return 0

    for address, label in wallets:
        s = survey(address)
        s["label"] = label
        out["wallets"][address] = s
    statuses = {k["status"] for w in out["wallets"].values() for k in w["kinds"].values()}
    out["result"] = "readable" if statuses & {"ok", "empty"} else "unsupported"
    save_latest(str(PROBE_DIR), out)

    print(f"[hyperevm-index] chain 999 via Etherscan V2: {out['result']}")
    for address, s in out["wallets"].items():
        kinds = ", ".join(f"{k}={v['rows']}({v['status']})" for k, v in s["kinds"].items())
        print(f"[hyperevm-index] {address[:12]}... ({s['label']}) {kinds} "
              f"ever_sent={s['sent_any']}")
        ranked = sorted(s["tokens"].values(), key=lambda t: -(t["in"] + t["out"]))
        for t in ranked[:10]:
            flag = "  <-- WOULD BE PRICED AT PAR" if t["would_be_priced_at_par"] else ""
            print(f"[hyperevm-index]    {t['symbol'][:18]:<18} {t['contract']} "
                  f"in={t['in']} out={t['out']}{flag}")
        if not ranked:
            print("[hyperevm-index]    no token transfers on record")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
