"""Transfer-level provenance shared by route discovery and movement accounting."""

import hashlib
import json
from decimal import Decimal, InvalidOperation


def transfer_binding(record):
    fields = {key: str(record.get(key) or '').lower()
              for key in ('id', 'chain', 'tx_hash', 'src', 'dst', 'token_address')}
    try:
        amount = Decimal(str(record.get('amount')))
        if not amount.is_finite() or amount <= 0:
            return None
    except InvalidOperation:
        return None
    if not all(fields.values()):
        return None
    fields['amount'] = str(amount.normalize())
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def bound_decode(record):
    """Legacy transaction decodes are clues, never proof for an individual leg."""
    decoded = record.get('route_decode')
    binding = transfer_binding(record)
    if (not binding or not isinstance(decoded, dict) or decoded.get('error')
            or decoded.get('heuristic') or decoded.get('transfer_binding') != binding):
        return {}
    return decoded
