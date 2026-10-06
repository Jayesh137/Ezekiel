# src/boundary/perimeter.py
"""His world as one lookup table: every address whose payment means him.

Built every trace run from what other detectors already MEASURED, never
inferred here, so a member's evidence is always another file's reading. Joining
edge events against a table costs one lookup per event, so the table can grow
without anything sweeping its members — the forward walk's scaling problem
(spec §2).

Roles are evidence weights, not identity. Ground truth stays config only
(CLAUDE.md): nothing here feeds spam immunity, cluster membership or `settled`.
Roster CONFIRMED is config-only by design, so it adds no role of its own.
"""

from __future__ import annotations

ROLE_WEIGHTS = {"core": 1.0, "deposit": 1.0, "identity": 1.0,
                "sink": 0.6, "funder": 0.6, "associate": 0.3}
ROLE_ORDER = ("core", "deposit", "identity", "sink", "funder", "associate")
STRONG_ROLES = ("core", "identity")
SINK_MIN_USD = 100_000.0
SINK_MIN_SHARE = 0.5
FUNDER_MIN_USD = 100_000.0
ASSOCIATE_MIN_USD = 1_000_000.0
HOT_MIN_USD = 100_000.0
ZERO = "0x" + "0" * 40
SYSTEM_PREFIXES = ("0x20000000000000000000000000000000000000", "0x2222222222")


def low(address) -> str:
    a = str(address or "").strip()
    return a.lower() if a.lower().startswith("0x") else a


def excluded(address, services) -> bool:
    a = low(address)
    return (not a or a == ZERO or a == "0x" + "f" * 40 or a in services
            or a.startswith(SYSTEM_PREFIXES))


def associates(records, core, *, is_contract, is_busy, services,
               min_usd: float = ASSOCIATE_MIN_USD) -> dict:
    """Large two-way personal counterparties of the core wallets. Pure."""
    core = {low(c) for c in core}
    services = {low(s) for s in services}
    flows: dict[str, dict] = {}
    for rec in records or []:
        usd = rec.get("amount_usd")
        if rec.get("spam") or usd is None:
            continue
        src, dst = low(rec.get("src")), low(rec.get("dst"))
        if src in core and dst not in core:
            flows.setdefault(dst, {"out": 0.0, "in": 0.0})["out"] += float(usd)
        elif dst in core and src not in core:
            flows.setdefault(src, {"out": 0.0, "in": 0.0})["in"] += float(usd)
    out = {}
    for addr, f in sorted(flows.items()):
        if f["out"] < min_usd or f["in"] < min_usd or excluded(addr, services):
            continue
        if is_contract(addr) or is_busy(addr):
            continue
        out[addr] = {"paid_him_usd": round(f["in"], 2), "he_paid_usd": round(f["out"], 2)}
    return out


def exchange_families(records, deposit_members, core, *, is_hot) -> dict:
    """Hot wallets behind his deposit addresses, and exchange wallets that paid him."""
    deposit_members = {low(d) for d in deposit_members}
    core = {low(c) for c in core}
    families: dict[str, set] = {}
    paid_him: dict[str, float] = {}
    for rec in records or []:
        if rec.get("spam"):
            continue
        src, dst = low(rec.get("src")), low(rec.get("dst"))
        usd = rec.get("amount_usd")
        if src in deposit_members and dst not in core and is_hot(dst):
            families.setdefault(src, set()).add(dst)
        elif dst in core and src not in core and usd is not None and is_hot(src):
            paid_him[src] = paid_him.get(src, 0.0) + float(usd)
    out = {f"deposit:{d}": sorted(h) for d, h in sorted(families.items())}
    withdraws = sorted(a for a, usd in paid_him.items() if usd >= HOT_MIN_USD)
    if withdraws:
        out["paid_him"] = withdraws
    return out


def family_index(families) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for fid, hots in sorted((families or {}).items()):
        for h in hots:
            index.setdefault(low(h), []).append(fid)
    return index


def build(*, config: dict, sentinels: dict, trace_report: dict, trace_registry: dict,
          solana: dict, associates_found: dict, families: dict, services,
          previous: dict | None, now_iso: str) -> dict:
    """The perimeter document. Pure."""
    services = {low(s) for s in services or ()}
    core = {low(config.get("target_wallet"))} | {low(w) for w in
                                                 config.get("known_self_wallets") or []}
    core.discard("")
    prior_members = (previous or {}).get("members") or {}
    members: dict[str, dict] = {}

    def add(address, role, why, source, **extra):
        a = low(address)
        if not a or (role != "core" and (a in core or excluded(a, services))):
            return
        old = members.get(a)
        if old is not None and ROLE_ORDER.index(old["role"]) <= ROLE_ORDER.index(role):
            old["sources"] = sorted(set(old["sources"]) | {source})
            return
        prior = prior_members.get(a) or {}
        members[a] = {"address": a, "role": role, "weight": ROLE_WEIGHTS[role], "why": why,
                      "sources": sorted(set((old or {}).get("sources") or []) | {source}),
                      "first_seen": prior.get("first_seen") or now_iso,
                      "hl": prior.get("hl"), **extra}

    for a in sorted(core):
        add(a, "core", "configured (ground truth)", "config")
    for a, s in sorted((sentinels or {}).items()):
        add(a, "deposit", "his private exchange deposit address: "
            + str((s or {}).get("reason") or "deposit sentinel"), "deposit_sentinels")
    for row in (trace_report or {}).get("deposit_addresses") or []:
        add(row.get("address"), "deposit", f"{row.get('kind')} the cluster paid "
            f"(hub {row.get('hub') or 'unknown'})", "trace_engine",
            hl_native=row.get("kind") == "hl_deposit")
    for addr, info in sorted((solana or {}).items()):
        if (info or {}).get("role") == "cluster":
            add(addr, "identity", "Solana address his CCTP burns minted to",
                "solana_addresses", raw=low((info or {}).get("mint_recipient_hex")) or None)
    for addr, info in sorted((trace_registry or {}).items()):
        money = (info or {}).get("his_money") or {}
        if ((info or {}).get("class") == "quiet_eoa"
                and float(money.get("share") or 0) >= SINK_MIN_SHARE
                and float(money.get("in_usd") or 0) >= SINK_MIN_USD):
            add(addr, "sink", f"quiet wallet holding ${float(money['in_usd']):,.0f} of his "
                f"money ({float(money['share']):.0%} of its inflow)", "trace_engine")
    for row in (trace_report or {}).get("funders") or []:
        if row.get("class") == "quiet_eoa" and float(row.get("paid_him_usd") or 0) >= FUNDER_MIN_USD:
            add(row.get("address"), "funder",
                f"quiet wallet that paid him ${float(row['paid_him_usd']):,.0f}", "trace_engine")
    for addr, row in sorted((associates_found or {}).items()):
        add(addr, "associate", f"two-way counterparty: paid him ${row['paid_him_usd']:,.0f}, "
            f"he paid ${row['he_paid_usd']:,.0f}", "substrate")

    counts: dict[str, int] = {}
    for m in members.values():
        counts[m["role"]] = counts.get(m["role"], 0) + 1
    return {"computed_at": now_iso, "members": dict(sorted(members.items())),
            "counts": counts, "exchange_families": families or {}}


def core_only(config: dict, now_iso: str) -> dict:
    """A working perimeter from config alone, for when no build has run yet."""
    return build(config=config, sentinels={}, trace_report={}, trace_registry={}, solana={},
                 associates_found={}, families={}, services=set(), previous=None,
                 now_iso=now_iso)


class Index:
    """O(1) membership by address or by raw (bytes32) form."""

    def __init__(self, perimeter: dict | None):
        self.members = dict((perimeter or {}).get("members") or {})
        self._by: dict[str, dict] = {}
        from src.circle_flows import base58_to_hex
        for a, m in self.members.items():
            self._by[low(a)] = m
            # A Solana member by its key too: a burn FROM it names the signer,
            # while `raw` is the token account Circle minted TO.
            for raw in (m.get("raw"), None if str(a).startswith("0x") else base58_to_hex(a)):
                if raw:
                    self._by.setdefault(low(raw), m)
        self.core = {a for a, m in self.members.items() if m.get("role") == "core"}
        self.families = family_index((perimeter or {}).get("exchange_families") or {})

    def get(self, address, raw=None) -> dict | None:
        for key in (low(address), low(raw)):
            if key and key in self._by:
                return self._by[key]
        return None
