#!/usr/bin/env python3
"""Can the Etherscan V2 key read HyperEVM (chain id 999)?

The public HyperEVM RPC caps log queries at 1000 blocks, so history there is
watched by nonce rather than swept. hyperevmscan is an Etherscan instance; if
the V2 key serves chain 999, the cluster's HyperEVM history becomes readable
like any other chain — including the $23M the target moved Core -> EVM that
is not at his address as Circle USDC. Locally the key is absent and the
answer cannot be had; this records it from CI.
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, etherscan_get, load_config, save_latest

PROBE_DIR = DATA_DIR / "hyperevm"


def main() -> int:
    config = load_config()
    target = config["target_wallet"]
    out = {"checked_at": datetime.now(UTC).isoformat(), "chain_id": 999,
           "key_present": bool(os.environ.get("ETHERSCAN_API_KEY"))}
    if not out["key_present"]:
        out["result"] = "no key — untested"
    else:
        payload = etherscan_get({"module": "account", "action": "tokentx",
                                 "address": target, "page": 1, "offset": 5,
                                 "sort": "desc"}, chain_id=999)
        out["status"] = payload.get("status")
        out["message"] = payload.get("message")
        rows = payload.get("result")
        out["rows"] = len(rows) if isinstance(rows, list) else None
        out["result"] = ("readable" if payload.get("status") == "1"
                         else "empty" if str(payload.get("message", "")).lower().startswith("no ")
                         else f"unsupported: {payload.get('result') or payload.get('message')}")
        if isinstance(rows, list) and rows:
            out["sample"] = [{k: r.get(k) for k in ("hash", "from", "to", "tokenSymbol", "value",
                                                     "timeStamp")} for r in rows[:3]]
    path = PROBE_DIR / "etherscan_probe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    save_latest(str(PROBE_DIR / "etherscan_probe"), out)
    print(f"[hyperevm-index] chain 999 via Etherscan V2: {out['result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
