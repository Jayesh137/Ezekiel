# src/withdrawals.py
"""Where each Hyperliquid withdrawal actually landed.

A `withdraw3` action names any Arbitrum destination, and the info API's ledger
record of it carries only the amount and the fee. A withdrawal to a fresh
address is therefore invisible to the tracer, which sweeps the target's own
address, and only fuzzily visible to the correlator. The bridge's outbound
USDC transfer is the other half of the record: same amount less the $1 fee,
minutes later, to the real destination.

Measured 2026-09-10: 146 unique withdrawals, $440.9M, and every one matched
a bridge transfer into his own Arbitrum address. The join works; a withdrawal
with no such match is money leaving to an address the system does not sweep.

Pure matching here; the one call that resolves an unmatched withdrawal's
destination lives behind an injectable fetch.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

WITHDRAWALS_DIR = DATA_DIR / "withdrawals"

# The bridge pays out within minutes; six hours absorbs any batching delay.
MATCH_WINDOW_S = 6 * 3600
# The paid amount is `usdc - fee`; a couple of dollars covers rounding.
AMOUNT_TOLERANCE = 2.0
# A withdrawal younger than this may simply not have been paid out yet.
SETTLING_S = 2 * 3600


def unique_withdrawals(ledger: list[dict]) -> list[dict]:
    """(hash, time_s, net amount) per withdrawal, deduplicated by hash."""
    seen = set()
    out = []
    for e in ledger or []:
        d = e.get("delta") or {}
        if d.get("type") != "withdraw":
            continue
        h = e.get("hash")
        if h in seen:
            continue
        seen.add(h)
        try:
            usdc = float(d.get("usdc") or 0)
            fee = float(d.get("fee") or 0)
            ts = int(e.get("time") or 0) // 1000
        except (TypeError, ValueError):
            continue
        if usdc <= 0 or not ts:
            continue
        out.append({"hash": h, "time_s": ts, "net_usd": round(usdc - fee, 2),
                    "gross_usd": usdc})
    return sorted(out, key=lambda w: w["time_s"])


def bridge_inbound(records: list[dict], bridge: str, wallet: str) -> list[dict]:
    """Substrate rows where the bridge paid the wallet."""
    b, w = (bridge or "").lower(), (wallet or "").lower()
    rows = []
    for r in records or []:
        if (r.get("src") or "").lower() == b and (r.get("dst") or "").lower() == w:
            if r.get("amount_usd") is not None:
                rows.append({"ts": int(r.get("ts") or 0), "usd": float(r["amount_usd"]),
                             "tx_hash": r.get("tx_hash")})
    return sorted(rows, key=lambda r: r["ts"])


def match(withdrawals: list[dict], inbound: list[dict],
          now_s: int | None = None) -> dict:
    """Pair each withdrawal with a bridge payout to the wallet's own address.

    Returns {"matched": [...], "unmatched": [...], "settling": [...]}. An
    unmatched withdrawal older than SETTLING_S went somewhere else.
    """
    now_s = now_s if now_s is not None else int(datetime.now(UTC).timestamp())
    used: set = set()
    matched, unmatched, settling = [], [], []
    for w in withdrawals:
        hit = None
        for i, r in enumerate(inbound):
            if i in used:
                continue
            if abs(r["usd"] - w["net_usd"]) <= AMOUNT_TOLERANCE and \
                    abs(r["ts"] - w["time_s"]) <= MATCH_WINDOW_S:
                hit = i
                break
        if hit is None:
            (settling if now_s - w["time_s"] < SETTLING_S else unmatched).append(w)
        else:
            used.add(hit)
            matched.append({**w, "payout_tx": inbound[hit]["tx_hash"]})
    return {"matched": matched, "unmatched": unmatched, "settling": settling}


def find_payout(withdrawal: dict, rows: list[dict], bridge: str) -> dict | None:
    """Among bridge token transfers around the withdrawal, the payout of its
    amount — and therefore its destination. `rows` are Etherscan tokentx rows."""
    b = (bridge or "").lower()
    best = None
    for r in rows or []:
        if (r.get("from") or "").lower() != b:
            continue
        try:
            usd = int(r.get("value") or 0) / 1e6
            ts = int(r.get("timeStamp") or 0)
        except (TypeError, ValueError):
            continue
        if abs(usd - withdrawal["net_usd"]) > AMOUNT_TOLERANCE:
            continue
        if not (0 <= ts - withdrawal["time_s"] <= MATCH_WINDOW_S):
            continue
        cand = {"destination": (r.get("to") or "").lower(), "tx_hash": r.get("hash"),
                "ts": ts, "usd": usd, "delay_s": ts - withdrawal["time_s"]}
        if best is None or cand["delay_s"] < best["delay_s"]:
            best = cand
    return best


def resolve_destinations(unmatched: list[dict], fetch_window, bridge: str,
                         max_lookups: int = 5) -> list[dict]:
    """Resolve the destination of each unmatched withdrawal.

    `fetch_window(time_s) -> (rows, error)` returns the bridge's token
    transfers from shortly before `time_s` forward. Failures are reported per
    withdrawal, never collapsed into "no destination".
    """
    out = []
    for w in unmatched[:max_lookups]:
        rows, err = fetch_window(w["time_s"])
        if err and not rows:
            out.append({**w, "destination": None, "status": f"unreadable: {err}"})
            continue
        hit = find_payout(w, rows, bridge)
        if hit is None:
            out.append({**w, "destination": None,
                        "status": "no payout of this amount found in the window"
                        + (f" (partial read: {err})" if err else "")})
        else:
            out.append({**w, **hit, "status": "resolved"})
    out.extend({**w, "destination": None, "status": "pending: lookup budget"}
               for w in unmatched[max_lookups:])
    return out


def build_report(withdrawals: list[dict], inbound: list[dict], resolved: list[dict],
                 now_s: int | None = None) -> dict:
    m = match(withdrawals, inbound, now_s)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "withdrawals": len(withdrawals),
        "withdrawn_usd": round(sum(w["gross_usd"] for w in withdrawals), 2),
        "matched_to_own_address": len(m["matched"]),
        "settling": len(m["settling"]),
        "unmatched": len(m["unmatched"]),
        "unmatched_usd": round(sum(w["gross_usd"] for w in m["unmatched"]), 2),
        "foreign": [r for r in resolved if r.get("destination")],
        "unresolved": [r for r in resolved if not r.get("destination")],
    }


def save(report: dict) -> None:
    save_latest(str(WITHDRAWALS_DIR), report)
