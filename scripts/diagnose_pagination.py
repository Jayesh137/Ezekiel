# scripts/diagnose_pagination.py
"""Find out why an Arbitrum erc20 sweep stops ~220 million blocks early.

Read-only. Writes nothing, commits nothing. Run it, read the log, delete it.

The measured failure, from the 2026-09-09 live runs:

    arbitrum erc20 collected blocks 185,763,477 .. 281,189,292
    the wallet has erc20 records out to           501,451,359
    the $13,000,000 movement sits at    468,825,716 .. 473,995,514
    walk_blocks reported truncated=False, gaps=0, error=None

So Etherscan returned a short page — fewer rows than `offset` — and the walk
correctly concluded it had finished, because a short page is the only "done"
signal the endpoint gives. Something makes it return short while more records
exist.

Rather than guess and add a retry, this asks the endpoint directly. Each probe
isolates one hypothesis, and every one of them prints what it actually got.

  H1  `endblock` is the limiter. We hardcode 99999999, which is BELOW Arbitrum's
      head (~501M). If that clamps the window, a larger endblock returns more.
      Confounder to watch: we already collected records above 99,999,999, which
      argues against a naive clamp — so this is worth measuring, not assuming.
  H2  A result cap. Etherscan caps a query at 10,000 rows; if the address is
      past that, asking from block 0 may return a bounded window and never
      advance past it however many times we resume.
  H3  Resume actually works and the first run simply stopped for another
      reason. Asking from the exact block we stopped at settles it.
  H4  `page` rather than `startblock` is the intended way deeper.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import etherscan_get, load_config

ARBITRUM = 42161
STOPPED_AT = 281_189_292          # where the live sweep believed it finished
KNOWN_NEWEST = 501_451_359        # what newest_block reported on the same run


def probe(label: str, params: dict) -> list[dict]:
    rows = []
    payload = etherscan_get({"module": "account", "action": "tokentx", **params},
                            chain_id=ARBITRUM)
    status, message = payload.get("status"), payload.get("message", "")
    result = payload.get("result")
    if isinstance(result, list):
        rows = result
    print(f"\n--- {label}")
    print(f"    params : { {k: v for k, v in params.items() if k != 'address'} }")
    print(f"    status : {status!r}  message: {message!r}")
    if not isinstance(result, list):
        print(f"    result : {str(result)[:120]!r}   <- not a list")
        return []
    if not rows:
        print("    rows   : 0")
        return []
    blocks = [int(r["blockNumber"]) for r in rows if str(r.get("blockNumber", "")).isdigit()]
    print(f"    rows   : {len(rows)}")
    if blocks:
        print(f"    blocks : {min(blocks):,} .. {max(blocks):,}")
        print(f"    past the stop point ({STOPPED_AT:,})? "
              f"{'YES' if max(blocks) > STOPPED_AT else 'no'}")
    return rows


def main() -> int:
    if not os.environ.get("ETHERSCAN_API_KEY"):
        print("ETHERSCAN_API_KEY absent — nothing to diagnose.")
        return 1
    target = load_config()["target_wallet"]
    base = {"address": target, "page": 1, "offset": 1000, "sort": "asc"}

    print(f"target      : {target}")
    print(f"stopped at  : {STOPPED_AT:,}")
    print(f"known newest: {KNOWN_NEWEST:,}")

    # H1 — is `endblock` the limiter?
    a = probe("H1a  from block 0, endblock=99999999 (what we ship)",
              {**base, "startblock": 0, "endblock": 99999999})
    probe("H1b  from block 0, endblock=999999999 (above chain head)",
          {**base, "startblock": 0, "endblock": 999999999})
    probe("H1c  from block 0, endblock=latest",
          {**base, "startblock": 0, "endblock": "latest"})

    # H3 — does resuming from where we stopped return anything past it?
    probe("H3a  resume from the stop point, endblock=99999999",
          {**base, "startblock": STOPPED_AT, "endblock": 99999999})
    probe("H3b  resume from the stop point, endblock=latest",
          {**base, "startblock": STOPPED_AT, "endblock": "latest"})

    # Does the tail exist at all, asked from the other end?
    probe("H3c  newest first (sort=desc, offset=5)",
          {**base, "startblock": 0, "endblock": "latest", "sort": "desc", "offset": 5})

    # H4 — is `page` the intended way deeper?
    if a:
        probe("H4   page=2 of the same window",
              {**base, "startblock": 0, "endblock": 99999999, "page": 2})

    # H2 — how far can resuming actually walk, given a real budget?
    print("\n--- H2  walk forward from block 0 and see where it stalls")
    start, seen, last_max = 0, 0, -1
    for i in range(1, 16):
        payload = etherscan_get({"module": "account", "action": "tokentx",
                                 "address": target, "startblock": start,
                                 "endblock": "latest", "page": 1,
                                 "offset": 1000, "sort": "asc"}, chain_id=ARBITRUM)
        rows = payload.get("result")
        if not isinstance(rows, list) or not rows:
            print(f"    call {i:>2}: 0 rows (status={payload.get('status')!r} "
                  f"msg={payload.get('message')!r}) -> walk ends")
            break
        blocks = [int(r["blockNumber"]) for r in rows
                  if str(r.get("blockNumber", "")).isdigit()]
        seen += len(rows)
        mx = max(blocks) if blocks else start
        print(f"    call {i:>2}: {len(rows):>4} rows, blocks {min(blocks):,} .. {mx:,}"
              f"{'   <- SHORT PAGE, walk would stop here' if len(rows) < 1000 else ''}")
        if mx <= last_max:
            print(f"    call {i:>2}: block did not advance ({mx:,}) -> would stall")
            break
        last_max, start = mx, mx
        if len(rows) < 1000:
            break
    print(f"\n    total rows seen: {seen}, furthest block: {last_max:,}")
    print(f"    reached the known newest ({KNOWN_NEWEST:,})? "
          f"{'YES' if last_max >= KNOWN_NEWEST else 'NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
