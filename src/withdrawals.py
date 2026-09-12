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

## The second withdrawal route: Circle

`withdraw3` is not the only way out. A spot `send` to `0x2000…0000` — the
HyperCore system address for USDC — is the ledger's whole trace of
`sendToEvmWithData`, Hyperliquid's native CCTP withdrawal, which mints USDC
at ANY recipient on ANY Circle chain minutes later. Found 2026-09-12: six of
them, $30M, described for months as "unaccounted for" while each sat in the
substrate as a mint from the zero address at his own Arbitrum address, in
the same minute, for the amount less Circle's $0.20.

The pairing is the same shape as the bridge one — amount, minutes, one of
his addresses — with two differences. The mint comes from the zero address
rather than the bridge, on whichever chain the domain named. And when there
is no mint at any cluster address, the stored explorer payload may still
name the recipient; when the payload has rolled out of the 300-action window
too, the money left to an address nobody can name, and that is reported
rather than counted as settled.

Pure matching here; the one call that resolves an unmatched withdrawal's
destination lives behind an injectable fetch.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.hl_actions import normalise_address
from src.utils import DATA_DIR, save_latest

WITHDRAWALS_DIR = DATA_DIR / "withdrawals"

# HyperCore's system address for spot token index 0 (USDC). A spot `send` to
# it is a Circle/CCTP withdrawal, not a transfer to HyperEVM.
SYSTEM_USDC = "0x2000000000000000000000000000000000000000"
# A CCTP mint is a Transfer from the zero address at the recipient.
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

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
            landed = inbound[hit]
            matched.append({**w, "payout_tx": landed["tx_hash"],
                            **{k: landed[k] for k in ("chain", "wallet") if k in landed}})
    return {"matched": matched, "unmatched": unmatched, "settling": settling}


def cctp_withdrawals(ledger: list[dict], wallet: str) -> list[dict]:
    """(hash, time_s, amount) per Circle withdrawal by `wallet`, deduplicated.

    The ledger row is a spot `send` whose destination is the USDC system
    address. Only USDC is priced here: the system address for another token
    index is a different address, and a token quantity is never a dollar
    value.
    """
    w = (wallet or "").lower()
    seen = set()
    out = []
    for e in ledger or []:
        d = e.get("delta") or {}
        if d.get("type") != "send" or (d.get("user") or "").lower() != w:
            continue
        if (d.get("destination") or "").lower() != SYSTEM_USDC:
            continue
        if str(d.get("token") or "").upper() != "USDC":
            continue
        h = e.get("hash")
        if h in seen:
            continue
        seen.add(h)
        try:
            amount = float(d.get("amount") or 0)
            fee = float(d.get("fee") or 0)
            ts = int(e.get("time") or 0) // 1000
        except (TypeError, ValueError):
            continue
        if amount <= 0 or not ts:
            continue
        out.append({"hash": h, "time_s": ts, "net_usd": round(amount - fee, 2),
                    "gross_usd": amount, "route": "cctp"})
    return sorted(out, key=lambda w: w["time_s"])


def cctp_inbound(records: list[dict], wallets) -> list[dict]:
    """Substrate rows where Circle minted USDC at one of `wallets`.

    A mint is a transfer from the zero address. Only priced, non-spam rows
    count: the value is what gets matched, and a counterfeit "USDC" minted by
    anyone is exactly the row that must not pair with a real withdrawal.
    """
    ws = {(w or "").lower() for w in wallets or [] if w}
    rows = []
    for r in records or []:
        if (r.get("src") or "").lower() != ZERO_ADDRESS:
            continue
        dst = (r.get("dst") or "").lower()
        if dst not in ws or r.get("spam") or r.get("amount_usd") is None:
            continue
        if str(r.get("asset") or "").upper() != "USDC":
            continue
        rows.append({"ts": int(r.get("ts") or 0), "usd": float(r["amount_usd"]),
                     "tx_hash": r.get("tx_hash"), "chain": r.get("chain"), "wallet": dst})
    return sorted(rows, key=lambda r: r["ts"])


def resolve_cctp(unmatched: list[dict], payloads: dict, cluster) -> dict:
    """Where an unpaired Circle withdrawal went, from its stored explorer
    payload. Pure.

    `payloads` maps action hash -> the recorded `sendToEvmWithData` (as
    hl_actions.own_actions shapes it). Three outcomes, kept apart:

      by_payload  the recipient is a cluster address — the mint is on a
                  chain the substrate does not read, but it is his
      foreign     the recipient is outside the cluster: the event this
                  project exists to catch
      unresolved  no payload survived the 300-action window and no mint at
                  any cluster address: money left to an address nobody can
                  name. Never counted as settled.
    """
    members = {normalise_address(c) for c in cluster or [] if c}
    by_payload, foreign, unresolved = [], [], []
    for w in unmatched:
        act = payloads.get(w.get("hash")) or {}
        dest = act.get("destination")
        if not dest:
            unresolved.append({**w, "destination": None, "chain": None,
                               "status": "payload rolled out of the explorer window and "
                                         "no mint of this amount at a cluster address"})
            continue
        row = {**w, "destination": dest, "chain": act.get("destination_chain")}
        if normalise_address(dest) in members:
            by_payload.append({**row, "status": "recipient is a cluster address (payload)"})
        else:
            foreign.append({**row, "status": "foreign"})
    return {"by_payload": by_payload, "foreign": foreign, "unresolved": unresolved}


def cctp_report(withdrawals: list[dict], inbound: list[dict], payloads: dict,
                cluster, now_s: int | None = None) -> dict:
    m = match(withdrawals, inbound, now_s)
    r = resolve_cctp(m["unmatched"], payloads, cluster)
    return {
        "withdrawals": len(withdrawals),
        "withdrawn_usd": round(sum(w["gross_usd"] for w in withdrawals), 2),
        "matched_to_cluster_mint": len(m["matched"]),
        "matched_by_payload": len(r["by_payload"]),
        "settling": len(m["settling"]),
        "foreign": r["foreign"],
        "unresolved": r["unresolved"],
        "unresolved_usd": round(sum(w["gross_usd"] for w in r["unresolved"]), 2),
        "landed": [{"hash": x["hash"], "chain": x.get("chain"), "wallet": x.get("wallet"),
                    "net_usd": x["net_usd"], "payout_tx": x.get("payout_tx")}
                   for x in m["matched"]],
    }


def cctp_lines(report: dict | None) -> list[str]:
    """Log lines for a CCTP report. Pure, and tolerant of every field being
    absent — a print path that raises after the work is done has already cost
    this project eighteen hours of a workflow."""
    r = report or {}
    n = r.get("withdrawals") or 0
    lines = [f"[withdrawals] circle: {n} CCTP withdrawal(s), "
             f"${(r.get('withdrawn_usd') or 0):,.0f}: {r.get('matched_to_cluster_mint') or 0} "
             f"minted at a cluster address, {r.get('matched_by_payload') or 0} named a cluster "
             f"address in the payload, {r.get('settling') or 0} settling, "
             f"{len(r.get('foreign') or [])} foreign, {len(r.get('unresolved') or [])} "
             f"unresolved (${(r.get('unresolved_usd') or 0):,.0f})"]
    lines.extend(f"[withdrawals] circle FOREIGN ${(f.get('net_usd') or 0):,.2f} -> "
                 f"{f.get('destination')} on {f.get('chain') or 'an unknown chain'}"
                 for f in r.get("foreign") or [])
    lines.extend(f"[withdrawals] circle UNRESOLVED ${(u.get('net_usd') or 0):,.2f} at "
                 f"{u.get('time_s')} ({u.get('hash')}): {u.get('status')}"
                 for u in r.get("unresolved") or [])
    return lines


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
