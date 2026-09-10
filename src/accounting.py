# src/accounting.py
"""How much of the money that left the target is actually accounted for.

Every other module answers "is this wallet his?". This one answers the question
underneath the whole project: of everything that left, how much do we know the
destination of, and how much did we lose?

Without it "follow every dollar" is unfalsifiable. The graph can look healthy —
nodes classified, confidence scored — while most of the money went somewhere
nothing on this dashboard mentions, and nothing would say so.

## Unpriced is not zero

1,359 stored records carry `amount_usd: None` — known majors (ETH, WETH, wstETH,
WBTC) the price source could not value, most of them outside its free 365-day
window. Summing them as 0.0 would report a tidy total that quietly omits real
money, which is the same failure as a missing price becoming a $0 transfer.

So unpriced outflows are counted and reported as a separate COUNT, never folded
into a dollar figure. A reconciliation that says "98% traced" while 700 ETH
transfers were invisible is worse than one that admits it cannot see them.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, load_config, save_latest

ACCOUNTING_DIR = DATA_DIR / "accounting"

# Where a dollar went, in decreasing order of "we know what this is".
DEST_SELF = "known_self"            # operator ground truth
DEST_IDENTIFIED = "identified"      # roster CONFIRMED or PROBABLE
DEST_INFRASTRUCTURE = "infrastructure"   # exchange / bridge / contract
DEST_LEAD = "lead"                  # roster POSSIBLE or WATCH
DEST_UNKNOWN = "unknown"            # in no roster tier at all

DEST_ORDER = [DEST_SELF, DEST_IDENTIFIED, DEST_INFRASTRUCTURE, DEST_LEAD,
              DEST_UNKNOWN]


def _roster_index() -> dict:
    try:
        with open(DATA_DIR / "roster" / "latest.json") as f:
            return {(w.get("wallet") or "").lower(): w
                    for w in json.load(f).get("wallets", [])}
    except (OSError, ValueError, AttributeError):
        return {}


def classify_destination(addr: str, roster: dict, known_self: set) -> str:
    """What we know about where this money went."""
    a = (addr or "").lower()
    if a in known_self:
        return DEST_SELF
    row = roster.get(a)
    if not row:
        return DEST_UNKNOWN
    tier = row.get("tier")
    if tier == "INFRASTRUCTURE":
        return DEST_INFRASTRUCTURE
    if tier in ("CONFIRMED", "PROBABLE"):
        return DEST_IDENTIFIED
    if tier in ("POSSIBLE", "WATCH"):
        return DEST_LEAD
    return DEST_UNKNOWN


def reconcile(records, target: str, roster: dict, known_self: set) -> dict:
    """Split the target's outflow by what we know about each destination.

    Pure: takes records, returns the accounting. `records` is whatever
    `records_for(target)` yields.
    """
    target = (target or "").lower()
    buckets = {k: {"usd": 0.0, "transfers": 0, "wallets": set()} for k in DEST_ORDER}
    unpriced = {"transfers": 0, "assets": {}, "wallets": set()}
    total_usd = 0.0

    for rec in records:
        if (rec.get("src") or "").lower() != target:
            continue
        dst = (rec.get("dst") or "").lower()
        if not dst:
            continue
        usd = rec.get("amount_usd")
        if usd is None:
            # Counted, never valued. Folding these in as 0.0 would report a
            # total that silently omits real money.
            unpriced["transfers"] += 1
            asset = rec.get("asset") or "?"
            unpriced["assets"][asset] = unpriced["assets"].get(asset, 0) + 1
            unpriced["wallets"].add(dst)
            continue
        try:
            usd = float(usd)
        except (TypeError, ValueError):
            continue
        bucket = buckets[classify_destination(dst, roster, known_self)]
        bucket["usd"] += usd
        bucket["transfers"] += 1
        bucket["wallets"].add(dst)
        total_usd += usd

    out = {"total_out_usd": round(total_usd, 2), "buckets": {}}
    for name in DEST_ORDER:
        b = buckets[name]
        out["buckets"][name] = {
            "usd": round(b["usd"], 2),
            "transfers": b["transfers"],
            "distinct_wallets": len(b["wallets"]),
            "share": round(b["usd"] / total_usd, 4) if total_usd > 0 else 0.0,
        }

    # "Traced" deliberately excludes leads. A POSSIBLE wallet is a question, and
    # counting questions as answers is how a reconciliation flatters itself.
    traced = (out["buckets"][DEST_SELF]["usd"]
              + out["buckets"][DEST_IDENTIFIED]["usd"]
              + out["buckets"][DEST_INFRASTRUCTURE]["usd"])
    out["traced_usd"] = round(traced, 2)
    out["traced_share"] = round(traced / total_usd, 4) if total_usd > 0 else 0.0
    out["unpriced"] = {
        "transfers": unpriced["transfers"],
        "distinct_wallets": len(unpriced["wallets"]),
        "assets": dict(sorted(unpriced["assets"].items(),
                              key=lambda kv: -kv[1])[:10]),
        "note": ("counted, never valued — folding these in as $0 would report a "
                 "total that omits real money"),
    }
    return out


def build_accounting(config: dict | None = None) -> dict:
    config = config or load_config()
    target = (config.get("target_wallet") or "").lower()
    known_self = {(w or "").lower() for w in config.get("known_self_wallets", [])}

    from src.chain.collect import records_for
    result = reconcile(records_for(target), target, _roster_index(), known_self)
    result["computed_at"] = datetime.now(UTC).isoformat()
    result["target"] = target
    return result


def main() -> int:
    acc = build_accounting()
    save_latest(str(ACCOUNTING_DIR), acc)
    print(f"[accounting] ${acc['total_out_usd']:,.2f} left the target across "
          f"priced transfers")
    for name in DEST_ORDER:
        b = acc["buckets"][name]
        if not b["transfers"]:
            continue
        print(f"[accounting]   {name:<16} ${b['usd']:>16,.2f}  "
              f"{b['share'] * 100:>5.1f}%  {b['distinct_wallets']} wallet(s)")
    print(f"[accounting] traced {acc['traced_share'] * 100:.1f}% "
          f"(self + identified + infrastructure; leads excluded on purpose)")
    up = acc["unpriced"]
    if up["transfers"]:
        print(f"[accounting] {up['transfers']} outbound transfer(s) could not be "
              f"valued and are NOT in the total: "
              + ", ".join(f"{a}x{n}" for a, n in list(up["assets"].items())[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
