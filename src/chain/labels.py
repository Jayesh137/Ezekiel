# src/chain/labels.py
"""Naming the things on the graph that are not people.

Before this module the system knew three service addresses: the Hyperliquid
bridge, the USDC contract and the zero address. A trail entering a Binance hot
wallet was therefore indistinguishable from a trail entering the trader's new
wallet — and worse, the frontier would happily spend its whole expansion budget
walking an exchange that receives from a million unrelated people.

Resolution order, strongest first:

  curated  — a human-checked entry in data/labels/entities.json
  code     — the address has bytecode, so it is not a person
  inferred — behaves like a CEX deposit address for one of our wallets
  fan      — many-to-many degree, the pre-existing heuristic

This strengthens the invariant in transfer_graph.py rather than bending it:
services still score 0.0, still never alert, still are never traversed through.
They are simply identified correctly now.
"""

import json
from pathlib import Path

SERVICE_CATEGORIES = {
    "cex_hot", "cex_deposit", "cex_deposit_sweep", "bridge", "dex_router",
    "contract", "mixer", "hl_infra", "service",
}

DEFAULT_FORWARD_RATIO = 0.95
DEFAULT_WINDOW_HOURS = 24.0
# A destination taking less than this share of what the address received is not
# evidence against it being a deposit address — deposit addresses pay gas too.
MATERIAL_DESTINATION_RATIO = 0.05


def load_registry_data(data: dict) -> dict[str, dict]:
    """Index a parsed entities document by lowercase address."""
    out: dict[str, dict] = {}
    for entry in data.get("entities", []):
        addr = (entry.get("address") or "").lower()
        if addr:
            out[addr] = entry
    return out


def load_registry(path: Path) -> dict[str, dict]:
    """The curated registry, or empty if it has not been created yet."""
    try:
        return load_registry_data(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return {}


def classify_address(addr: str, registry: dict[str, dict], *,
                     has_code: bool | None = None,
                     fan_reason: str | None = None,
                     inferred: dict | None = None) -> dict:
    """What this address is, and on what authority."""
    a = (addr or "").lower()

    entry = registry.get(a)
    if entry:
        return {"category": entry.get("category"), "entity": entry.get("entity"),
                "source": "curated", "is_service": True}

    if has_code is True:
        return {"category": "contract", "entity": None,
                "source": "code", "is_service": True}

    if inferred:
        return {"category": inferred.get("category", "cex_deposit"),
                "entity": inferred.get("entity"),
                "source": "inferred", "is_service": True}

    if fan_reason:
        return {"category": "service", "entity": fan_reason,
                "source": "fan_degree", "is_service": True}

    # has_code None means the lookup failed. Absence of evidence is not
    # evidence of a contract, so nothing is asserted.
    return {"category": None, "entity": None, "source": None, "is_service": False}


def infer_deposit_addresses(records: list[dict], cex_hot,
                            *, forward_ratio: float = DEFAULT_FORWARD_RATIO,
                            window_hours: float = DEFAULT_WINDOW_HOURS
                            ) -> dict[str, dict]:
    """Addresses that behave like an exchange deposit address.

    A CEX deposit address is never publicly labelled, and it is the highest
    value identity artifact on chain: it belongs to exactly one exchange
    account, so two wallets funding the same one are the same customer. Phase 2
    re-links on that; Phase 1 only has to name it.

    The signature is narrow on purpose — receives, then forwards nearly all of
    it to a known hot wallet, quickly, with no other material destination.
    """
    hot = {h.lower() for h in cex_hot}
    received: dict[str, float] = {}
    first_in: dict[str, int] = {}
    sent_to: dict[str, dict[str, float]] = {}
    # The single largest send to any hot wallet, per address — not the
    # earliest. An incidental small send ahead of the real transfer (a test
    # send, dust, a stray approval-adjacent transfer) must not be mistaken for
    # the forward that actually carries the value: anchoring the "quickly"
    # window to it would let a bulk forward days later pass as immediate.
    primary_out_to_hot: dict[str, tuple[float, int, str]] = {}

    for rec in records:
        usd = rec.get("amount_usd")
        if usd is None:
            continue
        usd = float(usd)
        src = (rec.get("src") or "").lower()
        dst = (rec.get("dst") or "").lower()
        ts = int(rec.get("ts", 0) or 0)

        if dst and dst not in hot:
            received[dst] = received.get(dst, 0.0) + usd
            if dst not in first_in or ts < first_in[dst]:
                first_in[dst] = ts
        if src and dst:
            sent_to.setdefault(src, {})
            sent_to[src][dst] = sent_to[src].get(dst, 0.0) + usd
            if dst in hot:
                current = primary_out_to_hot.get(src)
                if (current is None or usd > current[0]
                        or (usd == current[0] and ts < current[1])):
                    primary_out_to_hot[src] = (usd, ts, dst)

    out: dict[str, dict] = {}
    for addr, total_in in received.items():
        if total_in <= 0 or addr in hot:
            continue
        destinations = sent_to.get(addr) or {}
        to_hot = sum(v for d, v in destinations.items() if d in hot)
        if to_hot / total_in < forward_ratio:
            continue
        # The full total sent elsewhere, not just the largest single sibling —
        # ten destinations under the bar individually can still add up to real
        # activity that a deposit address would never have.
        other_total = sum(v for d, v in destinations.items() if d not in hot)
        if other_total / total_in > MATERIAL_DESTINATION_RATIO:
            continue
        primary = primary_out_to_hot.get(addr)
        if primary is None:
            continue
        _, out_ts, hot_addr = primary
        elapsed_hours = (out_ts - first_in.get(addr, out_ts)) / 3600.0
        if elapsed_hours < 0 or elapsed_hours > window_hours:
            continue
        out[addr] = {
            "category": "cex_deposit",
            "entity": f"deposit address forwarding to {hot_addr}",
            "forwarded_to": hot_addr,
            "forward_ratio": round(to_hot / total_in, 4),
            "hours_to_forward": round(elapsed_hours, 2),
        }
    return out


def service_addresses(registry: dict[str, dict], inferred: dict | None = None,
                      categories: set[str] | None = None) -> set[str]:
    """Addresses to treat as infrastructure, for a given purpose.

    `categories` defaults to all of SERVICE_CATEGORIES, which is right for
    traversal control. A caller asking a different question passes a narrower
    set — linkage does, because a cex_deposit address is evidence there, not
    infrastructure.

    `inferred` entries are all cex_deposit, so they are folded in only when the
    caller actually wants that category. Otherwise a future task wiring
    `inferred` through would quietly reintroduce the inversion.
    """
    wanted = SERVICE_CATEGORIES if categories is None else categories
    out = {a for a, e in registry.items() if e.get("category") in wanted}
    if "cex_deposit" in wanted:
        out |= {a.lower() for a in (inferred or {})}
    return out


class CodeCache:
    """Whether an address has bytecode. Asked once per address, ever.

    A failed lookup is not cached: recording "no code" because the API was down
    would permanently mislabel a contract as a person.
    """

    def __init__(self, path: Path, fetcher):
        self.path = Path(path)
        self._fetcher = fetcher
        try:
            self._table = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._table = {}

    def has_code(self, address: str, chain: dict) -> bool | None:
        key = f"{chain['name']}:{(address or '').lower()}"
        if key in self._table:
            return self._table[key]
        code = self._fetcher(address, chain)
        if code is None:
            return None
        self._table[key] = bool(code and code not in ("0x", "0X"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._table, indent=2, sort_keys=True))
        return self._table[key]
