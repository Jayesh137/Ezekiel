# src/boundary/attribution.py
"""Who sent value to his world, and whom his world funded. Pure.

Every edge feed — Bridge2, Circle, Unit, HL sends — is normalised to one event
shape and judged against the perimeter (spec §6.2). Votes follow the doctrine:
money-flow vectors are one family, so nothing here promotes a wallet alone.
Severity: CRITICAL is about HIM moving now; history, an unknown amount and an
address that only holds his money are HIGH; associates are recorded and never
buzz.
"""

from __future__ import annotations

from src.boundary.perimeter import STRONG_ROLES, SYSTEM_PREFIXES, low

DUST_USD = 100.0
SEVERITIES = ("CRITICAL", "HIGH")
KIND_PAID_HIS_WORLD = "outside_account_paid_his_world"
KIND_FUNDED_FROM_HIS_WORLD = "his_world_funded_outside_account"
KIND_HIS_ACCOUNT_PAID_OUTSIDE = "his_account_paid_outside_address"
KIND_MEMBER_ACTIVE = "perimeter_member_active_on_hl"
TITLES = {
    KIND_PAID_HIS_WORLD: "A Hyperliquid Account Outside His Cluster Paid His Address",
    KIND_FUNDED_FROM_HIS_WORLD: "His Address Funded A Hyperliquid Account Outside His Cluster",
    KIND_HIS_ACCOUNT_PAID_OUTSIDE: "His Account Sent Money To A New Address",
    KIND_MEMBER_ACTIVE: "An Address Holding His Money Is Active On Hyperliquid",
}
VOTE_TRANSFER, VOTE_LINKAGE = "transfer", "linkage"
HIGH_WEIGHT = 0.6
# Not accounts. A vault holds pooled money (hl_surface reads vaults); USDC's
# CCTP forwarder delivers every Circle deposit into Hyperliquid as a send, his
# own included — 27 of the first dry run's 30 findings (2026-10-06).
VAULT_KINDS = ("vaultDeposit", "vaultWithdraw")
PROTOCOL_ACCOUNTS = frozenset({"0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"})


def event(*, source, direction, hl_account, counterparty, chain, amount_usd, ts, ref,
          counterparty_raw=None, event_id=None, retro=False, **extra) -> dict:
    return {"source": source, "direction": direction, "hl_account": low(hl_account),
            "counterparty": low(counterparty), "counterparty_raw": low(counterparty_raw) or None,
            "chain": chain, "amount_usd": None if amount_usd is None else float(amount_usd),
            "ts": ts, "ref": ref, "event_id": event_id or f"{source}:{ref}", "retro": bool(retro),
            **extra}


def from_bridge2_withdrawal(row: dict, *, retro: bool = False) -> dict:
    from src.boundary.bridge2 import event_id
    return event(source="bridge2", direction="out", hl_account=row["user"],
                 counterparty=row["destination"], chain="arbitrum", amount_usd=row["usd"],
                 ts=row.get("ts"), ref=row.get("tx_hash"), event_id=event_id(row),
                 retro=retro, nonce=row.get("nonce"))


def from_bridge2_deposit(row: dict, *, retro: bool = False) -> dict:
    from src.boundary.bridge2 import event_id
    return event(source="bridge2", direction="in", hl_account=row["depositor"],
                 counterparty=row["depositor"], chain="arbitrum", amount_usd=row["usd"],
                 ts=row.get("ts"), ref=row.get("tx_hash"), event_id=event_id(row), retro=retro)


def from_hl_edges(edges, core, *, exclude=frozenset()) -> list[dict]:
    """Core-ledger edges as events for the OTHER account. Hubs and system
    addresses are not people: a token distributor's airdrop into his account
    (`0x3d855cf5…` sent him $6,000 of SENT) is not an account paying him."""
    core = {low(c) for c in core}
    exclude = {low(x) for x in exclude}
    out = []
    for e in edges or []:
        src, dst = low(e.get("src")), low(e.get("dst"))
        if src in core and dst not in core:
            other, direction = dst, "in"
        elif dst in core and src not in core:
            other, direction = src, "out"
        else:
            continue
        if (other in exclude or other in PROTOCOL_ACCOUNTS or other.startswith(SYSTEM_PREFIXES)
                or e.get("kind") in VAULT_KINDS):
            continue
        counterparty = src if direction == "in" else dst
        out.append(event(source="hl_send", direction=direction, hl_account=other,
                         counterparty=counterparty, chain="hyperliquid",
                         amount_usd=e.get("amount_usd"), ts=e.get("ts"),
                         ref=e.get("tx_hash"), event_id=e.get("id"), asset=e.get("asset")))
    return out


def finding_key(row: dict) -> str:
    return (f"{row.get('kind')}:{row.get('hl_account')}:"
            f"{row.get('counterparty') or row.get('counterparty_raw')}:"
            f"{row.get('ref') or row.get('event_id')}")


def _finding(ev: dict, kind: str, severity, vote, member) -> dict:
    if severity == "CRITICAL" and (ev.get("amount_usd") is None or ev.get("retro")):
        severity = "HIGH"      # unknown size, or history: never paged as a live move
    row = {**ev, "kind": kind, "severity": severity, "vote": vote,
           "role": (member or {}).get("role"), "member": (member or {}).get("address"),
           "member_why": (member or {}).get("why")}
    row["key"] = finding_key(row)
    return row


def classify(ev: dict, index) -> dict | None:
    account, counterparty = ev.get("hl_account"), ev.get("counterparty")
    raw = ev.get("counterparty_raw")
    if not account or not (counterparty or raw):
        return None
    usd = ev.get("amount_usd")
    if usd is not None and usd < DUST_USD:
        return None
    if usd is None and ev.get("source") == "hl_send" and ev.get("direction") == "out":
        return None     # an unpriced token sent INTO his world: anyone can airdrop one
    member = index.get(counterparty, raw)
    if account in index.core:
        if ev.get("direction") == "out" and counterparty != account and member is None:
            return _finding(ev, KIND_HIS_ACCOUNT_PAID_OUTSIDE, "CRITICAL", None, None)
        return None
    own = index.get(account)
    if account == counterparty or (own is not None and member is not None):
        # A member's own HL account moving money: to itself, or with his world.
        # A deposit address receiving his money is what makes it his.
        if own is None or (account != counterparty and own["role"] == "deposit"):
            return None
        severity = "HIGH" if own["weight"] >= HIGH_WEIGHT else None
        return _finding(ev, KIND_MEMBER_ACTIVE, severity, None, own)
    if member is None:
        return None
    role, weight = member["role"], member["weight"]
    if ev.get("direction") == "out":
        kind = KIND_PAID_HIS_WORLD
        if role in STRONG_ROLES:
            severity, vote = "CRITICAL", VOTE_TRANSFER
        elif role == "deposit":
            severity, vote = "CRITICAL", VOTE_LINKAGE
        else:
            severity, vote = ("HIGH" if weight >= HIGH_WEIGHT else None), None
    else:
        kind = KIND_FUNDED_FROM_HIS_WORLD
        if role in STRONG_ROLES:
            severity, vote = "CRITICAL", VOTE_TRANSFER
        else:
            severity, vote = ("HIGH" if weight >= HIGH_WEIGHT else None), None
    return _finding(ev, kind, severity, vote, member)


def classify_all(events, index) -> list[dict]:
    out, seen = [], set()
    for ev in events or []:
        f = classify(ev, index)
        if f and f["key"] not in seen:
            seen.add(f["key"])
            out.append(f)
    return out
