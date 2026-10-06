# src/boundary/measure.py
"""What the project has already measured about an address, read without spending.

Shared by the perimeter build, provenance and the correlator's exit classifier,
so all three judge "busy", "contract" and "service" by the same caches (rule 9:
an address with no reading is unmeasured, never quiet).
"""

from __future__ import annotations

import json
from pathlib import Path


def _read(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def load_services(config: dict, data_dir: Path) -> tuple[set, set]:
    """(services, labelled exchange hot wallets), read at call time.

    Labels and config only, as the trace engine's own list is. Never a roster
    tier: the roster calls his private deposit addresses INFRASTRUCTURE (they
    forward everything to an exchange), and reading that tier as "service"
    dropped all four from the perimeter. Busy and contract are measured.
    """
    from src.chain.labels import SERVICE_CATEGORIES, load_registry
    registry = load_registry(Path(data_dir) / "labels" / "entities.json")
    services = {a for a, e in registry.items()
                if e.get("category") in SERVICE_CATEGORIES - {"cex_deposit", "cex_deposit_sweep"}}
    services |= {(a or "").lower() for a in (config.get("known_service_addresses") or [])
                 + (config.get("excluded_addresses") or [])}
    hot = {a for a, e in registry.items() if e.get("category") == "cex_hot"}
    return services - {""}, hot


def measured(data_dir: Path):
    """(is_contract, is_busy, is_known) from the shared caches; never a lookup."""
    from src.chain.activity import MEASURABLE_CHAINS, ActivityCache, is_busy
    cache = ActivityCache(Path(data_dir) / "labels" / "address_activity.json", max_lookups=0)
    code = _read(Path(data_dir) / "labels" / "code_cache.json")

    def readings(a):
        a = (a or "").lower()
        return [r for r in (cache.cached(a, c) for c in MEASURABLE_CHAINS) if isinstance(r, dict)]

    def contract(a):
        a = (a or "").lower()
        return (any(code.get(f"{c}:{a}") is True for c in MEASURABLE_CHAINS)
                or any(r.get("is_contract") for r in readings(a)))

    def busy(a):
        return any(is_busy(r) for r in readings(a))

    def known(a):
        return bool(readings(a))
    return contract, busy, known
