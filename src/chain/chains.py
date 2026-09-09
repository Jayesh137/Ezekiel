"""Which chains we read, and how they are named.

One place knows chain names, ids and enablement, so every other module takes a
chain name and never a magic number. Etherscan V2 serves every chain here from
the same API key by varying `chainid`, which is why widening coverage costs no
new credentials.
"""

REQUIRED_KEYS = ("name", "chain_id", "native", "enabled", "priority")

DEFAULT_CHAINS = [
    {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0},
    {"name": "ethereum", "chain_id": 1, "native": "ETH", "enabled": True, "priority": 1},
    {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 2},
    {"name": "optimism", "chain_id": 10, "native": "ETH", "enabled": True, "priority": 3},
    {"name": "polygon", "chain_id": 137, "native": "POL", "enabled": True, "priority": 4},
    {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": True, "priority": 5},
]


def _validated(entry: dict) -> dict:
    missing = [k for k in REQUIRED_KEYS if k not in entry]
    if missing:
        raise ValueError(f"chain entry {entry!r} is missing {missing}")
    return entry


def _validated_chain_entries(config: dict) -> list[dict]:
    """Get chain entries from config, validated and falling back to defaults if omitted.

    Distinguishes between omitted key (returns defaults) and explicit empty list
    (returns empty list). All entries are validated upfront.
    """
    entries = DEFAULT_CHAINS if "chains" not in config else config["chains"]
    return [_validated(e) for e in entries]


def enabled_chains(config: dict) -> list[dict]:
    """Configured chains that are switched on, strongest priority first.

    A config written before this key existed simply omits it, so the defaults
    apply and Arbitrum keeps working exactly as before.
    """
    kept = _validated_chain_entries(config)
    return sorted((e for e in kept if e["enabled"]), key=lambda e: e["priority"])


def chain_by_name(name: str, config: dict) -> dict:
    """The chain entry for `name`, enabled or not. Raises KeyError if unknown."""
    wanted = (name or "").lower()
    entries = _validated_chain_entries(config)
    for entry in entries:
        if entry["name"].lower() == wanted:
            return entry
    raise KeyError(f"unknown chain: {name!r}")
