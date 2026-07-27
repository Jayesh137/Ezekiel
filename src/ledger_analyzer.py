# src/ledger_analyzer.py
"""Analyzes Hyperliquid-native transfers (send / internalTransfer / spotTransfer)
from the collected ledger to surface wallets the target moves funds to or from.

Why this exists: the tracer only watches Arbitrum L1 USDC. But the *easiest and
most private* way for the trader to move to a new wallet is entirely inside
Hyperliquid — an internalTransfer or spot `send` never touches L1, so the tracer
never sees it. This module reads the ledger data the collector already stores and
extracts the counterparties, ranking outbound destinations as migration leads.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    DATA_DIR,
    load_all_records,
    load_config,
    now_ms,
    read_cursor,
    save_latest,
    write_cursor,
)

# Ledger delta types that carry a wallet-to-wallet counterparty.
# accountClassTransfer (spot<->perp) and deposit/withdraw (L1) are intra-account
# or L1 and are intentionally excluded — they have no HL counterparty wallet.
COUNTERPARTY_TYPES = {"send", "internalTransfer", "spotTransfer"}


def _delta_usd(delta: dict) -> float:
    """USD value of a transfer delta. internalTransfer is USDC-denominated;
    send/spotTransfer carry a usdcValue for the token amount."""
    for key in ("usdc", "usdcValue"):
        v = delta.get(key)
        if v not in (None, "", "0", "0.0"):
            try:
                return abs(float(v))
            except (TypeError, ValueError):
                continue
    return 0.0


def build_counterparties(ledger: list[dict], target: str, excluded: set,
                         known_self: set, min_track: float = 1000) -> list[dict]:
    """Pure aggregation: turn ledger records into ranked HL-native counterparties.

    Outbound destinations (target -> other) are the migration-relevant ones: money
    leaving to a wallet that then starts trading is the signal we care about most.
    Separated from I/O so it can be unit-tested without disk or config.
    """
    target = target.lower()
    excluded = {a.lower() for a in excluded}
    known_self = {a.lower() for a in known_self}

    # counterparty -> aggregate
    parties: dict[str, dict] = {}
    for entry in ledger:
        delta = entry.get("delta", {})
        if delta.get("type") not in COUNTERPARTY_TYPES:
            continue
        sender = (delta.get("user") or "").lower()
        receiver = (delta.get("destination") or "").lower()
        if not sender or not receiver or sender == receiver:
            continue
        # Only transfers that involve the target on exactly one side.
        if target not in (sender, receiver):
            continue
        counterparty = receiver if sender == target else sender
        if not counterparty or counterparty in excluded or counterparty == target:
            continue

        direction = "out" if sender == target else "in"
        usd = _delta_usd(delta)
        ts = int(entry.get("time", 0) or 0)
        token = delta.get("token", "USDC")

        p = parties.setdefault(counterparty, {
            "wallet": counterparty,
            "total_out_usd": 0.0,   # target -> counterparty
            "total_in_usd": 0.0,    # counterparty -> target
            "transfer_count": 0,
            "first_seen_ms": ts or now_ms(),
            "last_seen_ms": ts,
            "tokens": set(),
            "known_self": counterparty in known_self,
        })
        p["total_out_usd" if direction == "out" else "total_in_usd"] += usd
        p["transfer_count"] += 1
        if ts:
            p["first_seen_ms"] = min(p["first_seen_ms"], ts) if p["first_seen_ms"] else ts
            p["last_seen_ms"] = max(p["last_seen_ms"], ts)
        if token:
            p["tokens"].add(token)

    counterparties = []
    for p in parties.values():
        total = p["total_out_usd"] + p["total_in_usd"]
        if total < min_track and not p["known_self"]:
            continue
        bidirectional = p["total_out_usd"] > 0 and p["total_in_usd"] > 0
        # Migration relevance: heavily weight OUTBOUND (funds leaving to this
        # wallet), reward recency, and reward a two-way relationship (a wallet
        # the target both funds and receives from is very likely the same owner).
        recency_days = (now_ms() - p["last_seen_ms"]) / 86_400_000 if p["last_seen_ms"] else 999
        recency_factor = max(0.2, 1.0 - recency_days / 90)
        relevance = (
            p["total_out_usd"] * 1.0 + p["total_in_usd"] * 0.4
        ) * recency_factor
        if bidirectional:
            relevance *= 1.5
        counterparties.append({
            "wallet": p["wallet"],
            "total_out_usd": round(p["total_out_usd"], 2),
            "total_in_usd": round(p["total_in_usd"], 2),
            "total_usd": round(total, 2),
            "transfer_count": p["transfer_count"],
            "bidirectional": bidirectional,
            "known_self": p["known_self"],
            "tokens": sorted(p["tokens"]),
            "first_seen": datetime.fromtimestamp(p["first_seen_ms"] / 1000, tz=UTC).isoformat() if p["first_seen_ms"] else None,
            "last_seen": datetime.fromtimestamp(p["last_seen_ms"] / 1000, tz=UTC).isoformat() if p["last_seen_ms"] else None,
            "last_seen_ms": p["last_seen_ms"],
            "relevance": round(relevance, 2),
        })

    counterparties.sort(key=lambda c: c["relevance"], reverse=True)
    return counterparties


def analyze_hl_transfers() -> dict:
    """Load the ledger, aggregate HL-native counterparties, and persist the result."""
    config = load_config()
    target = config["target_wallet"].lower()
    excluded = set(config.get("excluded_addresses", []))
    known_self = set(config.get("known_self_wallets", []))
    min_track = config.get("hl_transfer", {}).get("min_usdc_track", 1000)

    ledger = load_all_records(str(DATA_DIR / "ledger"))
    counterparties = build_counterparties(ledger, target, excluded, known_self, min_track)

    result = {
        "computed_at": datetime.now(UTC).isoformat(),
        "target": target,
        "counterparty_count": len(counterparties),
        "counterparties": counterparties,
    }
    # Only latest.json — the full transfer history already lives in data/ledger,
    # so per-run snapshots would just bloat the repo with near-identical files.
    save_latest(str(DATA_DIR / "hl_transfers"), result)
    return result


def check_new_outbound_transfers(result: dict | None = None) -> list[dict]:
    """Alert when the target makes a NEW significant outbound HL-native transfer to
    an unknown wallet. Uses a timestamp cursor so each transfer alerts at most once.
    Returns the list of newly-alerted counterparties."""
    from src.alerts import alert_hl_native_transfer

    if result is None:
        result = analyze_hl_transfers()

    config = load_config()
    min_alert = config.get("hl_transfer", {}).get("min_usdc_alert", 50000)
    cursor = read_cursor("last_hl_transfer_alert_ms")
    max_seen = cursor
    newly_alerted = []

    for c in result.get("counterparties", []):
        # Known-linked wallets never fire the "new wallet" alert (we already know
        # them) and must NOT advance the cursor — their transfers are frequent and
        # recent, and letting them push the cursor forward could mask a genuinely
        # new non-self outbound whose transfer is collected a little later.
        if c["known_self"]:
            continue
        # Only fire for outbound funds to a fresh wallet not seen before this cursor.
        # The per-wallet 72h alert cooldown is the backstop against repeats.
        if c["total_out_usd"] < min_alert:
            continue
        if c["last_seen_ms"] <= cursor:
            continue
        if alert_hl_native_transfer(
            c["wallet"], c["total_out_usd"], c["total_in_usd"],
            c["bidirectional"], c["tokens"],
        ):
            newly_alerted.append(c)
            # Only a DELIVERED alert may advance the cursor. This used to sit
            # outside the branch, so a failed send still moved the watermark past
            # the transfer and the alert was never retried — the same defect that
            # silently retired an undelivered transfer-graph discovery.
            max_seen = max(max_seen, c["last_seen_ms"])
        else:
            print(f"[ledger_analyzer] alert NOT delivered for {c['wallet'][:12]}... "
                  f"— cursor held at {cursor} so it is retried next run")

    if max_seen > cursor:
        write_cursor("last_hl_transfer_alert_ms", max_seen)

    return newly_alerted


def main():
    result = analyze_hl_transfers()
    print(f"[ledger_analyzer] {result['counterparty_count']} HL-native counterparties")
    for c in result["counterparties"][:10]:
        tag = " [known-self]" if c["known_self"] else ""
        print(f"[ledger_analyzer]   {c['wallet']}{tag}: "
              f"out=${c['total_out_usd']:,.0f} in=${c['total_in_usd']:,.0f} "
              f"({c['transfer_count']} transfers, relevance={c['relevance']:,.0f})")
    alerted = check_new_outbound_transfers(result)
    if alerted:
        print(f"[ledger_analyzer] Fired {len(alerted)} new-outbound alert(s)")


if __name__ == "__main__":
    main()
