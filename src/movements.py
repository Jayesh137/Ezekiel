"""Reconcile exact economic routes before asking which unknown account received funds.

This module never infers ownership from amount or timing. A decoded destination
is a route endpoint; only operator-configured cluster members are trusted selves.
"""

import hashlib
import json
import math
import re

from src.route_binding import bound_decode

# A cluster outbound whose money returns as a similar-size deposit to his own HL
# account is a round-trip (his Aave/Monad-style DeFi yield loops), not an exit.
# Only an OBSERVED return resolves it, so a genuine exit — which by definition does
# not come back — is never hidden (rule 7: reach beats tidiness).
ROUNDTRIP_WINDOW_S = 45 * 86400
ROUNDTRIP_TOLERANCE = 0.02


def positive_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def event_identity(record: dict) -> str:
    if record.get("id"):
        return str(record["id"])
    raw = json.dumps(record, sort_keys=True, default=str, separators=(",", ":"))
    return "transfer:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def reconcile_movements(records: list[dict], ledger: list[dict], cluster: set[str],
                        bridge_decodes: dict, min_amount: float = 0,
                        withdrawal_destinations: dict | None = None) -> dict:
    """`withdrawal_destinations` maps a withdrawal's nonce to Bridge2's own
    FinalizedWithdrawal (src/boundary/bridge2.py): the ledger's `withdraw` row
    names no destination, so without it $370M of the target's withdrawals to
    himself read as unresolved exits (measured 2026-10-06)."""
    cluster = {str(wallet).lower() for wallet in cluster if wallet}
    movements, seen, clean = [], set(), []
    coverage = {"unpriced_or_invalid": 0, "ignored_spam": 0,
                "exact_withdrawal_pairs": 0, "basis": "stored_observations_only"}
    for row in records or []:
        if not isinstance(row, dict):
            continue
        if row.get("spam"):
            coverage["ignored_spam"] += 1
            continue
        identity = event_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        clean.append((identity, row))

    exact_payouts = {}
    for identity, row in clean:
        reference = row.get("withdrawal_hash") or row.get("hl_withdrawal_hash")
        if reference:
            exact_payouts.setdefault(str(reference).lower(), []).append((identity, row))
    ledger_seen = set()
    for entry in ledger or []:
        if not isinstance(entry, dict):
            continue
        delta = entry.get("delta") or {}
        if not isinstance(delta, dict) or delta.get("type") != "withdraw":
            continue
        ref = str(entry.get("hash") or "").lower()
        if not ref or ref in ledger_seen:
            continue
        ledger_seen.add(ref)
        amount = positive_number(delta.get("usdc"))
        ts = positive_number(entry.get("time"))
        if amount is None or ts is None:
            coverage["unpriced_or_invalid"] += 1
            continue
        pairs = exact_payouts.get(ref, [])
        destinations = {str(row.get("dst") or "").lower() for _, row in pairs}
        destination = str(delta.get("destination") or "").lower()
        exact = (withdrawal_destinations or {}).get(str(delta.get("nonce")))
        if exact and not pairs:
            destination = str(exact.get("destination") or "").lower()
        if len(destinations) == 1 and "" not in destinations:
            destination = next(iter(destinations))
        conflict = len(destinations) > 1
        resolved = bool(destination and destination in cluster and not conflict)
        if resolved:
            coverage["exact_withdrawal_pairs"] += 1
        movements.append({"id": f"hl-withdraw:{ref}", "ref": ref, "source": "hl_withdraw",
                          "source_wallet": str(delta.get("user") or "").lower(),
                          "destination": destination or None, "amount": amount,
                          "ts": int(ts) // 1000, "event_ids": [f"hl:{ref}", *[p[0] for p in pairs]],
                          "resolution": ("conflicting_payouts" if conflict else
                                         "withdrawal_to_cluster" if resolved else "unresolved"),
                          "route_resolved": resolved, "known_self": resolved})

    for identity, row in clean:
        source = str(row.get("src") or "").lower()
        if source not in cluster:
            continue
        destination = str(row.get("dst") or "").lower()
        amount, ts = positive_number(row.get("amount_usd")), positive_number(row.get("ts"))
        if amount is None or ts is None:
            coverage["unpriced_or_invalid"] += 1
            continue
        ref = str(row.get("tx_hash") or "").lower()
        decoded = bound_decode(row)
        resolution, endpoint = "unresolved", destination or None
        if destination in cluster:
            resolution = "cluster_internal"
        elif decoded and not decoded.get("heuristic") and not decoded.get("error"):
            recipient = str(decoded.get("hl_account") or decoded.get("recipient") or "").lower()
            if recipient in cluster:
                resolution, endpoint = "bridge_to_cluster", recipient
            elif decoded.get("hl_account") and re.fullmatch(r"0x[0-9a-f]{40}", recipient):
                resolution, endpoint = "bridge_to_account", recipient
        movements.append({"id": identity, "ref": ref, "source": "l1_outbound",
                          "source_wallet": source, "destination": endpoint,
                          "immediate_destination": destination, "chain": row.get("chain"),
                          "amount": amount, "ts": int(ts), "event_ids": [identity],
                          "resolution": resolution, "route_resolved": resolution != "unresolved",
                          "known_self": resolution in {"cluster_internal", "bridge_to_cluster"},
                          "decoded_route": decoded or None})

    # Return-leg reconciliation. His HL deposits, oldest first, each consumable
    # once. An unresolved outbound followed within the window by a deposit of the
    # same size back into his account is a round-trip, not money that left him.
    deposits = []
    for entry in ledger or []:
        if not isinstance(entry, dict):
            continue
        delta = entry.get("delta") or {}
        if not isinstance(delta, dict) or delta.get("type") != "deposit":
            continue
        usdc, ts = positive_number(delta.get("usdc")), positive_number(entry.get("time"))
        if usdc is not None and ts is not None:
            deposits.append([int(ts) // 1000, usdc, False])
    deposits.sort()
    for mv in movements:
        if mv["route_resolved"] or mv["source"] != "l1_outbound":
            continue
        for dep in deposits:
            if dep[2] or dep[0] <= mv["ts"] or dep[0] > mv["ts"] + ROUNDTRIP_WINDOW_S:
                continue
            if abs(dep[1] - mv["amount"]) <= ROUNDTRIP_TOLERANCE * mv["amount"]:
                dep[2] = True
                mv.update(resolution="self_roundtrip", route_resolved=True,
                          known_self=True, return_deposit_ts=dep[0])
                break

    movements.sort(key=lambda item: (item["ts"], item["id"]))
    resolved = [row for row in movements if row["route_resolved"]]
    unresolved = [row for row in movements if not row["route_resolved"] and row["amount"] >= min_amount]
    return {"movements": movements, "resolved": resolved, "unresolved_exits": unresolved,
            "coverage": coverage,
            "totals": {"route_resolved_usd": round(sum(row["amount"] for row in resolved), 2),
                       "unresolved_exit_usd": round(sum(row["amount"] for row in unresolved), 2),
                       "note": "Gross observed movements, not distinct capital or ownership proof."}}
