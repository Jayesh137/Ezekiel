"""Trace exact routes while keeping funder, protocol caller and recipient distinct."""

from src.candidate_registry import valid_wallet
from src.movements import event_identity
from src.route_binding import bound_decode, transfer_binding


def _matches_source_burn(record, message):
    """Bind raw token movement and sender roles, never a converted dollar value."""
    from decimal import Decimal, InvalidOperation
    token = str(message.get('burn_token') or '').lower()
    if len(token) != 66 or not token.startswith('0x' + '0' * 24):
        return False
    burned = _address('0x' + token[-40:])
    sender = _address(message.get('message_sender'))
    if (not burned or burned != _address(record.get('token_address')) or not sender
            or sender not in {_address(record.get('src')), _address(record.get('dst'))}):
        return False
    try:
        amount, burn = Decimal(str(record.get('amount'))), Decimal(str(message.get('amount_usd')))
        return amount.is_finite() and burn.is_finite() and amount > 0 and amount == burn
    except InvalidOperation:
        return False


def decode_source_messages(payload, tx_hash, source_domain):
    """Decode attested V2 messages returned for a specific source transaction.

    An attestation's completion is not destination credit confirmation.
    """
    import re

    from src.chain.bridges import _hook_account
    from src.circle_flows import decode_burn_body

    if not isinstance(payload, dict) or str(payload.get("sourceTxHash", "")).lower() != tx_hash.lower():
        return []
    out = []
    for index, row in enumerate(payload.get("messages") or []):
        if not isinstance(row, dict) or row.get("status") != "complete":
            continue
        raw = str(row.get("message") or "").removeprefix("0x").lower()
        if len(raw) < 296 + 456 or not re.fullmatch(r"[0-9a-f]+", raw):
            continue
        if int(raw[:8], 16) != 1 or int(raw[8:16], 16) != source_domain or int(raw[24:88], 16) == 0:
            continue
        body = decode_burn_body(raw[296:])
        if not body:
            continue
        out.append({"event_id": f"cctp-source:{source_domain}:{tx_hash}:{index}",
                    "source_tx_hash": tx_hash.lower(), "source_domain": source_domain,
                    "destination_domain": int(raw[16:24], 16),
                    "protocol_message_id": f"{source_domain}:0x{raw[24:88]}",
                    "protocol_id_verified": True, "protocol": "cctp",
                    "message_sender": body["message_sender"],
                    "original_funder": None, "recipient": body["mint_recipient"],
                    "amount_usd": body["amount_usd"], "burn_token": body["burn_token"],
                    "hl_account": _hook_account(body["hook"]) if int(raw[16:24], 16) == 19 else None,
                    "credit_confirmed": False})
    return out


def resolve_source_routes(records, decodes, store, *, fetch=None, max_queries=20, seconds=60):
    """Bounded free Iris lookups for known CCTP source transactions."""
    import time

    import requests

    from src.chain.bridges import CCTP_DOMAINS

    domains = {name: number for number, name in CCTP_DOMAINS.items()}
    cached = {}
    for event in store.observations("cctp_sources"):
        cached.setdefault((event["source_domain"], event["source_tx_hash"]), []).append(event)
    enriched, output, errors, spent = [], dict(decodes), [], 0
    attempts = store.meta('source_route_attempts', {})
    by_transaction = {}
    ordered = sorted(records, key=lambda row: (attempts.get(f"{row.get('chain')}:{row.get('tx_hash', '').lower()}", 0),
                                               -(row.get('ts') or 0), event_identity(row)))
    for record in ordered:
        by_transaction.setdefault((record.get('chain'), (record.get('tx_hash') or '').lower()), {})[event_identity(record)] = record
    deadline = time.monotonic() + seconds
    for record in ordered:
        tx, chain = (record.get("tx_hash") or "").lower(), record.get("chain")
        decoded = decodes.get(f"{chain}:{tx}", decodes.get(tx)) or {}
        domain = domains.get(chain)
        if decoded.get("protocol") not in ("cctp", "cctp_extension") or domain is None:
            enriched.append(record)
            continue
        key = (domain, tx)
        if key not in cached and spent < max_queries and time.monotonic() < deadline:
            spent += 1
            attempts[f'{chain}:{tx}'] = int(time.time() * 1000)
            try:
                if fetch:
                    payload = fetch(domain, tx)
                else:
                    response = requests.get(f"https://iris-api.circle.com/v2/messages/{domain}",
                                            params={"transactionHash": tx}, timeout=(10, 20))
                    response.raise_for_status()
                    payload = response.json()
                cached[key] = decode_source_messages(payload, tx, domain)
                store.ingest_observations("cctp_sources", cached[key], int(time.time() * 1000))
                if not cached[key]:
                    errors.append({"tx": tx, "reason": "no validated complete V2 message"})
            except Exception as exc:
                cached[key] = []
                errors.append({"tx": tx, "reason": str(exc)[:200]})
        messages = cached.get(key, [])
        # A transaction can contain multiple burns and fees. Only a unique
        # matching token movement supports assigning a message to this record.
        matching = [m for m in messages if _matches_source_burn(record, m)]
        if len(matching) == 1 and sum(_matches_source_burn(r, matching[0])
                                     for r in by_transaction[(chain, tx)].values()) != 1:
            matching = []
        if len(matching) == 1:
            message = matching[0]
            enriched.append({**record, "protocol_message_id": message["protocol_message_id"],
                             "protocol_id_verified": True,
                             "route_decode": {**decoded, **message,
                                              "protocol_id_verified": True,
                                              "transfer_binding": transfer_binding(record)}})
        elif messages:
            # A transaction-level recipient cannot rescue an unbound movement:
            # batch burns, another token or multiple matching legs stay open.
            enriched.append({**record, 'route_decode': {'protocol': decoded.get('protocol'),
                                                       'error': 'source_message_not_bound'}})
        else:
            enriched.append({**record, 'route_decode': {'protocol': decoded.get('protocol'),
                                                       'error': 'source_message_unavailable'}})
    if spent:
        store.set_meta('source_route_attempts', dict(sorted(attempts.items(), key=lambda item: item[1], reverse=True)[:10000]))
    return enriched, output, {"queries": spent, "errors": errors}


def _address(value):
    try:
        return valid_wallet(value)
    except ValueError:
        return None


def index_routes(records, bridge_decodes, circle_events, cluster):
    cluster = {w.lower() for w in cluster}
    messages = {}
    for row in circle_events:
        if row.get("protocol_id_verified") and row.get("protocol_message_id"):
            messages.setdefault(row["protocol_message_id"], []).append(row)
    routes, unresolved, discoveries, seen = [], [], {}, set()

    def discover(wallet, route):
        # A mint (the zero address), a chain's native-token system contract or a
        # HyperCore system address is not a funder: data/candidates/0x000…0000.json
        # held hundreds of these before 2026-10-06.
        if (not wallet or wallet in cluster or wallet == "0x" + "0" * 40
                or wallet == "0x0000000000000000000000000000000000001010"
                or wallet.startswith(("0x20000000000000000000000000000000000000", "0x2222222222"))):
            return
        key = (wallet, route["id"])
        discoveries[key] = {"wallet": wallet, "source": "funding_route", "positive": True,
                            "event_id": route["id"], "parent_event_ids": route["parent_event_ids"],
                            "assertion": route["assertion"], "confirms_owner": False}

    for rec in records:
        source = _address(rec.get("src"))
        destination = _address(rec.get("dst"))
        if (source not in cluster and destination not in cluster) or rec.get("spam") or rec.get("value_basis") == "impostor":
            continue
        event_id = event_identity(rec)
        if event_id in seen:
            continue
        seen.add(event_id)
        chain, tx = rec.get("chain"), (rec.get("tx_hash") or "").lower()
        decoded = bound_decode(rec)
        if source and source not in cluster and destination in cluster:
            # This is the observed immediate transfer source, never an inferred
            # original customer behind an exchange or shared bridge.
            route = {"id": event_id, "original_funder": source, "recipient": destination,
                     "source_chain": chain, "source_tx": tx, "amount_usd": rec.get("amount_usd"),
                     "ts_ms": int((rec.get("ts") or 0) * 1000), "assertion": "observed_transfer",
                     "parent_event_ids": [event_id], "confirms_owner": False}
            routes.append(route)
            discover(source, route)
            continue
        matches = messages.get(decoded.get("protocol_message_id"), []) if decoded.get("protocol_id_verified") else []
        matches = [m for m in matches if m.get("direction") == "in"]
        endpoints = {_address(m.get("hl_account")) for m in matches} - {None}
        instructed = _address(decoded.get("hl_account"))
        if instructed:
            endpoints.add(instructed)
        base = {"id": event_id, "original_funder": source, "source_chain": chain,
                "source_tx": tx, "transaction_caller": rec.get("transaction_caller"),
                "protocol": decoded.get("protocol"), "amount_usd": rec.get("amount_usd"),
                "ts_ms": int((rec.get("ts") or 0) * 1000),
                "parent_event_ids": [event_id], "confirms_owner": False}
        if len(endpoints) == 1:
            recipient = next(iter(endpoints))
            matching = [m for m in matches if _address(m.get("hl_account")) == recipient]
            parents = [event_id] + [str(m.get("event_id") or event_identity(m)) for m in matching]
            route = {**base, "hl_account": recipient, "recipient": recipient, "destination_chain": "hyperliquid",
                     "message_sender": matching[0].get("counterparty") if matching else None,
                     "parent_event_ids": sorted(set(parents)),
                     "assertion": "protocol_route" if matching else "funding_instruction",
                     "match_method": "protocol_message_id" if matching else "exact_source_decode",
                     "credit_confirmed": any(m.get("credit_confirmed") is True for m in matching)}
            routes.append(route)
            discover(recipient, route)
        else:
            recipient = _address(decoded.get("recipient")) or _address(rec.get("dst"))
            if recipient in cluster:
                routes.append({**base, "recipient": recipient, "assertion": "internal_route"})
                continue
            unresolved.append({**base, "boundary": recipient or rec.get("dst"),
                               "last_exact_event": event_id,
                               "reason": "conflicting_recipients" if len(endpoints) > 1 else "destination_not_joined",
                               "missing_evidence": "recipient/source transaction tied to a protocol identifier",
                               "next_query": "retrieve source receipt and destination message; inspect recipient forwarding",
                               "alternatives": sorted(endpoints)})

    for row in circle_events:
        account, counterparty = _address(row.get("hl_account")), _address(row.get("counterparty"))
        if not account or (account not in cluster and counterparty not in cluster):
            continue
        event_id = str(row.get("event_id") or event_identity(row))
        if event_id in seen:
            continue
        seen.add(event_id)
        route = {"id": event_id, "hl_account": account, "counterparty": counterparty,
                 "message_sender": row.get("message_sender", counterparty if row.get("direction") == "in" else account),
                 "original_funder": row.get("original_funder"), "direction": row.get("direction"),
                 "amount_usd": row.get("amount_usd"), "parent_event_ids": [event_id],
                 "assertion": "protocol_route", "confirms_owner": False,
                 "credit_confirmed": row.get("credit_confirmed") is True}
        routes.append(route)
        if counterparty in cluster:
            discover(account, route)
    return {"routes": routes, "unresolved": unresolved, "discoveries": list(discoveries.values()),
            "coverage": {"transfer_records": len(records), "circle_events": len(circle_events)},
            "counts": {"routes": len(routes), "unresolved": len(unresolved), "discoveries": len(discoveries)}}
