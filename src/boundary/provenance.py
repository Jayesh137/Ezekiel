# src/boundary/provenance.py
"""Where did this account's money come from? Pure; every read is injected.

Spec §7. An HL account's ledger names how value entered it (Bridge2, Circle,
Unit, HyperEVM, or a send from another account); each entry is followed back
one hop to whoever funded it, and a quiet or unmeasured funder of real size one
hop further, classifying every source against the perimeter. Measured
2026-10-06 on the leads: 0x5b5d5120 and 0xb83de012 share their exchange hot
wallets with the target and one quiet funder with each other; 0xdd53c529 was
funded from the Binance hot wallet his own deposit address forwards into.
A source that could not be read is UNREADABLE, never "no source" (rule 5).
"""

from __future__ import annotations

from src.boundary.perimeter import STRONG_ROLES, SYSTEM_PREFIXES, low
from src.boundary.readers import HOSTS, NoReader, ReadError

FORWARDER = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"
ROUTE_BRIDGE2, ROUTE_CIRCLE, ROUTE_UNIT = "bridge2", "circle", "unit"
ROUTE_HYPEREVM, ROUTE_HL_SEND = "hyperevm", "hl_send"
EXCHANGE_CLASSES = ("exchange", "busy")
MOVES = ("send", "spotTransfer", "internalTransfer", "subAccountTransfer", "vaultWithdraw")
MIN_ENTRY_USD = 10_000.0
MAX_ENTRIES = 6
MAX_SOURCES = 6
LOOKBACK_S = 30 * 86400
HOP2_MIN_SHARE, HOP2_MIN_USD = 0.2, 50_000.0
UNIT_MATCH_S = 3600
DUST_USD = 100.0
HIGH_WEIGHT = 0.6


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _usd(delta: dict, token: str) -> float | None:
    """Same rule as trace.hl_ledger: usdcValue, never a token quantity (rule 11)."""
    amount = _num(delta.get("amount")) if delta.get("amount") is not None else _num(delta.get("usdc"))
    value = _num(delta.get("usdcValue"))
    if value is not None:
        if value == 0 and (amount or 0) > 0 and token != "USDC":
            return None
        return abs(value)
    if token == "USDC" and amount is not None:
        return abs(amount)
    return None


def _entry(route, usd, ts, ref, source, chain, **extra) -> dict:
    return {"route": route, "usd": usd, "ts": ts, "ref": ref, "source": source,
            "source_chain": chain, **extra}


def _unit_match(token: str, ts: int, unit_in: list) -> dict | None:
    t = (token or "").upper()
    if len(t) < 2 or not t.startswith("U"):
        return None
    best = None
    for e in unit_in:
        if (e.get("asset") or "") != t[1:].lower() or not e.get("ts"):
            continue
        gap = abs(int(e["ts"]) - ts)
        if gap <= UNIT_MATCH_S and (best is None or gap < best[0]):
            best = (gap, e)
    return best[1] if best else None


def route_entries(ledger, account, *, unit_events=()) -> list[dict]:
    """Every value-bearing inbound ledger row, with the route it arrived by."""
    account = low(account)
    unit_in = [e for e in unit_events or [] if e.get("direction") == "in"
               and low(e.get("hl_account")) == account]
    out = []
    for row in ledger or []:
        delta = (row or {}).get("delta") or {}
        kind = delta.get("type")
        try:
            ts = int(row.get("time") or 0) // 1000
        except (TypeError, ValueError):
            continue
        ref = row.get("hash")
        if kind == "deposit":
            out.append(_entry(ROUTE_BRIDGE2, _num(delta.get("usdc")), ts, ref, account, "arbitrum"))
            continue
        if kind not in MOVES:
            continue
        if kind == "vaultWithdraw":
            src, dst, token = low(delta.get("vault")), low(delta.get("user")) or account, "USDC"
            usd = _num(delta.get("netWithdrawnUsd"))
            usd = usd if usd is not None else _num(delta.get("requestedUsd"))
        else:
            src, dst = low(delta.get("user")), low(delta.get("destination"))
            token = str(delta.get("token") or "USDC")
            usd = _usd(delta, token)
        if dst != account or not src or src == account:
            continue
        if src == FORWARDER:
            out.append(_entry(ROUTE_CIRCLE, usd, ts, ref, None, None))
        elif src.startswith(SYSTEM_PREFIXES):
            out.append(_entry(ROUTE_HYPEREVM, usd, ts, ref, account, "hyperevm"))
        else:
            op = _unit_match(token, ts, unit_in)
            if op:
                out.append(_entry(ROUTE_UNIT, usd, ts, ref, op["counterparty"], op["chain"],
                                  unit_ref=op.get("ref"), via=src))
            else:
                out.append(_entry(ROUTE_HL_SEND, usd, ts, ref, src, "hyperliquid"))
    return sorted(out, key=lambda e: e["ts"])


def significant(entries, *, min_usd: float = MIN_ENTRY_USD, limit: int = MAX_ENTRIES) -> list:
    keep = [e for e in entries or [] if (e["usd"] is not None and e["usd"] >= min_usd)
            or (e["usd"] is None and e["route"] == ROUTE_UNIT)]
    return sorted(keep, key=lambda e: -(e["usd"] or 0))[:limit]


def aggregate(transfers) -> list[dict]:
    """Inbound transfers grouped by sender. Dust (valued < $100) is the shape of
    address poisoning, and a sender Blockscout flags as a scam is not a funder."""
    by: dict[str, dict] = {}
    for t in transfers or []:
        usd = t.get("usd")
        if t.get("from_is_scam") or not t.get("from") or (usd is not None and usd < DUST_USD):
            continue
        a = by.setdefault(t["from"], {"address": t["from"], "chain": t.get("chain"), "usd": 0.0,
                                      "unvalued": 0, "count": 0, "first_ts": t["ts"],
                                      "last_ts": t["ts"],
                                      "is_contract": bool(t.get("from_is_contract"))})
        a["count"] += 1
        if usd is None:
            a["unvalued"] += 1
        else:
            a["usd"] += float(usd)
        a["first_ts"], a["last_ts"] = min(a["first_ts"], t["ts"]), max(a["last_ts"], t["ts"])
    return sorted(by.values(), key=lambda a: -a["usd"])


def classify_source(address, chain, *, index, label_of, is_contract: bool = False) -> dict:
    member = index.get(address)
    if member:
        return {"class": "perimeter", "role": member["role"], "weight": member["weight"],
                "member_why": member.get("why"), "member": member["address"]}
    out: dict = {}
    label = label_of(address, chain)
    family = index.families.get(low(address))
    if family:
        out["family"], label = family, "exchange"
    if label is None and is_contract:
        label = "contract"
    out["class"] = label or "unmeasured"
    return out


def _single(address, chain, usd, ts, kind) -> list[dict]:
    return [{"address": low(address), "chain": chain, "usd": usd, "count": 1,
             "unvalued": int(usd is None), "first_ts": ts, "last_ts": ts, "kind": kind}]


def _hop1(entry, account, read_inbound, read_first_gas, circle_source):
    route, ts = entry["route"], entry["ts"]
    if route == ROUTE_BRIDGE2:
        rows = aggregate(read_inbound("arbitrum", account, since_ts=ts - LOOKBACK_S, until_ts=ts))
        gas = read_first_gas("arbitrum", account)
        if gas:
            hit = next((r for r in rows if r["address"] == gas["from"]), None)
            if hit:
                hit["kind"] = "first_gas"
            else:
                rows += _single(gas["from"], "arbitrum", None, gas["ts"], "first_gas")
        return rows
    if route == ROUTE_HYPEREVM:
        return aggregate(read_inbound("hyperevm", account, since_ts=ts - LOOKBACK_S, until_ts=ts))
    if route == ROUTE_CIRCLE:
        src = circle_source(entry, account)
        return _single(src["address"], src["chain"], entry["usd"], ts, "circle_sender") if src else None
    if route == ROUTE_UNIT:
        return _single(entry["source"], entry["source_chain"], entry["usd"], ts, "unit_source")
    return _single(entry["source"], "hyperliquid", entry["usd"], ts, "hl_sender")


def _hop2(source, read_inbound, read_ledger) -> list[dict]:
    if source["chain"] == "hyperliquid":
        rows = []
        for e in significant(route_entries(read_ledger(source["address"]), source["address"])):
            if e["route"] in (ROUTE_HL_SEND, ROUTE_UNIT) and e["source"]:
                rows += _single(e["source"], e["source_chain"], e["usd"], e["ts"],
                                "hl_sender" if e["route"] == ROUTE_HL_SEND else "unit_source")
        return [r for r in rows if r["address"] != source["address"]]
    if source["chain"] not in HOSTS:
        raise NoReader(f"no keyless reader for {source['chain']}")
    return aggregate(read_inbound(source["chain"], source["address"],
                                  since_ts=(source.get("first_ts") or 0) - LOOKBACK_S,
                                  until_ts=source.get("last_ts") or source.get("first_ts") or 0))


def resolve(account, *, ledger, unit_events, index, label_of, read_inbound, read_first_gas,
            read_ledger, circle_source, now_ts) -> dict:
    account = low(account)
    record = {"account": account, "resolved_at": now_ts, "entries": [], "sources": [],
              "unreadable": [], "complete": True}
    record["entries"] = significant(route_entries(ledger, account, unit_events=unit_events))
    for entry in record["entries"]:
        try:
            hop1 = _hop1(entry, account, read_inbound, read_first_gas, circle_source)
        except ReadError as exc:
            hop1, why, transient = None, str(exc)[:160], not isinstance(exc, NoReader)
        else:
            why, transient = "source not resolved", False
        if hop1 is None:
            record["unreadable"].append({"hop": 1, "entry": entry["ref"], "error": why,
                                         "transient": transient})
            record["complete"] = False
            continue
        total = sum(s["usd"] for s in hop1 if s.get("usd"))
        for s in hop1[:MAX_SOURCES]:
            info = classify_source(s["address"], s["chain"], index=index, label_of=label_of,
                                   is_contract=s.get("is_contract", False))
            record["sources"].append({**s, **info, "hop": 1, "entry": entry["ref"],
                                      "route": entry["route"]})
            big = ((s.get("usd") or 0) >= HOP2_MIN_USD
                   and (not total or s["usd"] / total >= HOP2_MIN_SHARE))
            if info["class"] not in ("quiet", "unmeasured") or not big:
                continue
            try:
                hop2 = _hop2(s, read_inbound, read_ledger)
            except ReadError as exc:
                record["unreadable"].append({"hop": 2, "via": s["address"], "error": str(exc)[:160],
                                             "transient": not isinstance(exc, NoReader)})
                continue
            for t in hop2[:MAX_SOURCES]:
                info2 = classify_source(t["address"], t["chain"], index=index, label_of=label_of,
                                        is_contract=t.get("is_contract", False))
                record["sources"].append({**t, **info2, "hop": 2, "via": s["address"],
                                          "entry": entry["ref"], "route": entry["route"]})
    # A throttled or budget-cut read is this run's problem, not the account's:
    # the record is kept (its findings stand) and re-resolved next run.
    record["retry"] = any(u.get("transient") for u in record["unreadable"])
    record["exchange_sources"] = sorted({s["address"] for s in record["sources"]
                                         if s["class"] in EXCHANGE_CLASSES})
    record["verdict"] = verdict(record)
    return record


def verdict(record: dict) -> str:
    sources = record.get("sources") or []
    if any(s["class"] == "perimeter" and s.get("weight", 0) >= HIGH_WEIGHT for s in sources):
        return "touches_his_world"
    if any(s.get("family") for s in sources):
        return "same_exchange"
    if not record.get("complete"):
        return "unresolved"
    if any(s["class"] in EXCHANGE_CLASSES for s in sources if s.get("hop") == 1):
        return "exchange"
    return "unrelated"


def findings(record: dict) -> list[dict]:
    entries = {e["ref"]: e for e in record.get("entries") or []}
    out, seen = [], set()
    for s in record.get("sources") or []:
        if s.get("class") != "perimeter":
            continue
        role, weight, hop = s["role"], s.get("weight", 0), s["hop"]
        if hop == 1 and role in STRONG_ROLES:
            severity, vote = "CRITICAL", "transfer"
        elif weight >= HIGH_WEIGHT:
            severity, vote = "HIGH", None
        else:
            severity, vote = None, None
        e = entries.get(s.get("entry")) or {}
        key = f"provenance:{record['account']}:{s['address']}:{s.get('entry')}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": "provenance_touches_his_world", "account": record["account"],
                    "severity": severity, "vote": vote, "hop": hop, "role": role,
                    "member": s.get("member") or s["address"], "member_why": s.get("member_why"),
                    "via": s.get("via"),
                    "route": s.get("route"), "usd": e.get("usd"), "ts": e.get("ts"),
                    "entry_ref": s.get("entry"), "key": key})
    return out


def shared_funders(records: dict) -> list[dict]:
    by: dict[str, set] = {}
    for account, rec in (records or {}).items():
        for s in (rec or {}).get("sources") or []:
            if s.get("hop") == 1 and s.get("class") == "quiet":
                by.setdefault(s["address"], set()).add(account)
    return [{"funder": f, "accounts": sorted(a)} for f, a in sorted(by.items()) if len(a) >= 2]
