# src/trace/patterns.py
"""Shapes in the flow that name a relationship. Pure functions only.

* A **hub** (exchange hot wallet, distributor, bridge account) is boundary, not
  a wallet: walking it learns nothing about him.
* A **deposit address inside Hyperliquid**: exchanges give each customer a
  HyperCore address that forwards every deposit, whole and within seconds, to
  one hub. Found 2026-10-04: `0x4aecac3b…` received 140,777 UENA from
  `0xf078969e…` and forwarded the identical amount 11 seconds later to
  `0x1f6093d3…`. Like an L1 deposit address it belongs to ONE exchange account,
  so anyone else paying it is the same customer — the strongest single signal
  in the project, now on the side of the venue the L1 search cannot see.
* A **shared quiet payee**: two wallets paying the same quiet wallet within days
  of each other. `0x793a3e8a…` and `0xf078969e…` both funded `0x734c9213…`
  (5 transactions in its life) within two hours on 2025-09-20; nothing scored it.
"""

from __future__ import annotations

from src.ledger_analyzer import counterparty_spread, hl_service_reason

HUB_DEGREE = 25          # matches ledger_analyzer / transfer_graph service fan degree
SATURATED_ROWS = 2000    # a full first ledger page: far busier than any person
FORWARD_WINDOW_S = 600   # an exchange sweeps a deposit address in seconds
PAYEE_WINDOW_S = 7 * 86400
MIN_LINK_EDGE_USD = 100.0
# A single-purpose wallet is paid by a handful of hands. 0x734c9213 had two;
# 0x160f6ef9, a busy personal wallet the first production dry run linked
# fourteen strangers through, had dozens.
MAX_PAYEE_SENDERS = 4
ZERO = "0x0000000000000000000000000000000000000000"

_MOVES = ("send", "spotTransfer")


def _low(a) -> str:
    return (a or "").strip().lower()


def is_hub(ledger, address: str) -> bool:
    """Whether this HL account's own ledger shows a service, not a person."""
    rows = [r for r in ledger or [] if isinstance(r, dict)]
    if len(rows) >= SATURATED_ROWS:
        return True
    out_degree, in_degree = counterparty_spread(rows, address)
    return hl_service_reason(out_degree, in_degree, HUB_DEGREE, HUB_DEGREE) is not None


def deposit_hub(ledger, address: str, *, window_s: int = FORWARD_WINDOW_S) -> str | None:
    """The hub this address forwards every deposit to, or None.

    Every inbound move must leave again whole — same token, same amount — to one
    destination within `window_s`. A partial forward, a slow one, or a second
    destination is a wallet that spends, not a deposit address.
    """
    me = _low(address)
    ins, outs = [], []
    for row in ledger or []:
        delta = (row or {}).get("delta") or {}
        if delta.get("type") not in _MOVES:
            continue
        src, dst = _low(delta.get("user")), _low(delta.get("destination"))
        item = (int(row.get("time") or 0), str(delta.get("token") or "USDC"),
                str(delta.get("amount")), src, dst)
        if dst == me and src != me:
            ins.append(item)
        elif src == me and dst != me:
            outs.append(item)
    if not ins or not outs:
        return None
    hubs = {o[4] for o in outs}
    if len(hubs) != 1:
        return None
    used = set()
    for t_in, token, amount, _src, _dst in sorted(ins):
        match = next((i for i, o in enumerate(outs)
                      if i not in used and o[1] == token and o[2] == amount
                      and 0 <= o[0] - t_in <= window_s * 1000), None)
        if match is None:
            return None
        used.add(match)
    if len(used) != len(outs):
        return None
    return hubs.pop()


def shared_payees(edges, *, cluster, quiet, boundaries=(), window_s: int = PAYEE_WINDOW_S,
                  min_edge_usd: float = MIN_LINK_EDGE_USD,
                  max_senders: int = MAX_PAYEE_SENDERS) -> list[dict]:
    """Outsiders who paid a quiet wallet within `window_s` of one of his paying it.

    `quiet` holds payees MEASURED quiet (rule 9: unmeasured is never quiet); a
    deposit address belongs to `linkage`, so callers keep those out. Unvalued
    and dust edges link nobody, a payee with more than `max_senders` distinct
    senders is not single-purpose, and a boundary (exchange, contract, mint
    address) sending is not a person choosing to pay it.
    """
    cluster = {_low(c) for c in cluster}
    boundaries = {_low(b) for b in boundaries} | {ZERO}
    quiet = {_low(q) for q in quiet} - cluster
    by_payee: dict[str, list[tuple[int, str, float]]] = {}
    for e in edges or []:
        dst, src = _low(e.get("dst")), _low(e.get("src"))
        usd = e.get("amount_usd")
        if dst not in quiet or not src or src == dst or usd is None:
            continue
        if float(usd) < min_edge_usd:
            continue
        by_payee.setdefault(dst, []).append((int(e.get("ts") or 0), src, float(usd)))

    links = []
    for payee, rows in sorted(by_payee.items()):
        his = [(ts, usd) for ts, src, usd in rows if src in cluster]
        if not his or len({src for _, src, _ in rows}) > max_senders:
            continue
        outsiders: dict[str, dict] = {}
        for ts, src, usd in rows:
            if src in cluster or src in boundaries:
                continue
            gap = min(abs(ts - t) for t, _ in his)
            row = outsiders.setdefault(src, {"usd": 0.0, "gap": gap})
            row["usd"] += usd
            row["gap"] = min(row["gap"], gap)
        for wallet, row in sorted(outsiders.items()):
            if row["gap"] > window_s:
                continue
            links.append({"wallet": wallet, "via": payee,
                          "outsider_usd": round(row["usd"], 2),
                          "cluster_usd": round(sum(u for _, u in his), 2),
                          "gap_hours": round(row["gap"] / 3600, 2)})
    return links


L1_FORWARD_WINDOW_S = 24 * 3600
L1_FORWARD_RATIO = 0.95


def l1_deposit_hot(edges, address: str, *, hot, window_s: int = L1_FORWARD_WINDOW_S,
                   ratio: float = L1_FORWARD_RATIO) -> str | None:
    """The exchange hot wallet an L1 address forwards every deposit to, or None.

    Judged deposit by deposit: each valued inflow must be swept on to one hot
    wallet within `window_s` of arriving, nearly all value must leave there, and
    nothing material may go anywhere else. `chain.labels.infer_deposit_addresses`
    measures from the first inflow to the largest forward instead, which calls a
    deposit address used for weeks a wallet (0x841b9e4f, his, 2023).
    """
    me = _low(address)
    hot = {_low(h) for h in hot}
    ins, outs = [], []
    for e in edges or []:
        usd = e.get("amount_usd")
        if usd is None or float(usd) < MIN_LINK_EDGE_USD:
            continue
        src, dst = _low(e.get("src")), _low(e.get("dst"))
        if dst == me and src != me:
            ins.append((int(e.get("ts") or 0), float(usd)))
        elif src == me and dst != me:
            outs.append((int(e.get("ts") or 0), float(usd), dst))
    if not ins or not outs:
        return None
    total_in = sum(u for _, u in ins)
    to_hot = [o for o in outs if o[2] in hot]
    hubs = {o[2] for o in to_hot}
    if len(hubs) != 1 or sum(o[1] for o in to_hot) / total_in < ratio:
        return None
    if sum(o[1] for o in outs if o[2] not in hot) / total_in > 1 - ratio:
        return None
    sweeps = sorted(o[0] for o in to_hot)
    for t_in, _usd in ins:
        if not any(0 <= t - t_in <= window_s for t in sweeps):
            return None
    return hubs.pop()
