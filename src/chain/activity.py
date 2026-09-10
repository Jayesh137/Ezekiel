# src/chain/activity.py
"""How busy an address is on the whole chain, not just in our substrate.

The address-reuse vector rests on a destination belonging to ONE account. The
substrate can only count senders among wallets it has swept, so a router used
by two million people looked like a five-sender private address: measured
2026-09-10, SocketGateway (2.19M transactions), the Aave aToken, the CCTP
TokenMinter and Paraswap were all counted as "shared deposit addresses", and a
personal DeFi wallet with 84 substrate counterparties was graded a service.

Blockscout answers the global question without an API key: one call returns
whether the address has code and how many transactions and token transfers it
has ever had. That number is what separates an exchange hot wallet (millions)
from a person (hundreds or thousands) and from a deposit address (tens).

Every lookup is cached. Activity only grows, so a reading is re-taken only
after it ages; a failed read is never cached, and a caller must treat None as
"could not tell", never as "quiet".
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

# Above this many transactions plus token transfers an address is shared
# infrastructure whatever its bytecode says. Exchange hot wallets and routers
# sit in the hundreds of thousands to millions; the busiest personal wallet in
# the cluster (0x236f233d..., a heavy DeFi user) is under 4,000.
BUSY_ACTIVITY = 20_000

# Re-read an address this long after its last reading. A quiet address can
# become a router; a router never becomes quiet.
REFRESH_DAYS = 30

HOSTS = {
    "arbitrum": "https://arbitrum.blockscout.com",
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
}

CACHE_NAME = "address_activity.json"


def fetch_activity(address: str, chain: str, *, get=None, timeout: float = 30.0) -> dict | None:
    """One address on one chain: {"is_contract", "txs", "token_transfers", "name"}.

    None when the chain has no Blockscout host or either call failed. A 404 is
    a real answer — the address has never been seen there — and is returned as
    zero activity rather than None.
    """
    host = HOSTS.get((chain or "").lower())
    if not host:
        return None
    get = get or requests.get
    a = (address or "").lower()
    headers = {"accept": "application/json"}
    try:
        r = get(f"{host}/api/v2/addresses/{a}", timeout=timeout, headers=headers)
        if r.status_code == 404:
            return {"is_contract": False, "txs": 0, "token_transfers": 0, "name": None}
        if r.status_code != 200:
            return None
        info = r.json()
        c = get(f"{host}/api/v2/addresses/{a}/counters", timeout=timeout, headers=headers)
        if c.status_code != 200:
            return None
        counters = c.json()
        return {
            "is_contract": bool(info.get("is_contract")),
            "txs": int(counters.get("transactions_count") or 0),
            "token_transfers": int(counters.get("token_transfers_count") or 0),
            "name": info.get("name"),
        }
    except Exception:                                 # noqa: BLE001 - transport
        return None


def is_busy(activity: dict | None) -> bool | None:
    """True for shared infrastructure, False for a quiet address, None if unknown."""
    if not activity:
        return None
    return (int(activity.get("txs") or 0) + int(activity.get("token_transfers") or 0)) >= BUSY_ACTIVITY


class ActivityCache:
    """Per-(chain, address) readings on disk, refreshed only when stale."""

    def __init__(self, path: Path, fetcher=None, *, max_lookups: int = 30,
                 sleep=time.sleep, now=None):
        self.path = Path(path)
        self._fetch = fetcher or fetch_activity
        self.max_lookups = int(max_lookups)
        self.lookups = 0
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        try:
            self._table = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._table = {}
        if not isinstance(self._table, dict):
            self._table = {}

    @staticmethod
    def key(address: str, chain: str) -> str:
        return f"{(chain or '').lower()}:{(address or '').lower()}"

    def _fresh(self, entry: dict) -> bool:
        try:
            checked = datetime.fromisoformat(entry["checked_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return (self._now() - checked).days < REFRESH_DAYS

    def cached(self, address: str, chain: str) -> dict | None:
        """The stored reading, fresh or stale, without spending a lookup."""
        return self._table.get(self.key(address, chain))

    def get(self, address: str, chain: str) -> dict | None:
        """A fresh reading, spending one lookup if needed. None if unavailable."""
        k = self.key(address, chain)
        entry = self._table.get(k)
        if entry and self._fresh(entry):
            return entry
        if self.lookups >= self.max_lookups:
            return entry  # stale is better than nothing, and is reported as such
        self.lookups += 1
        got = self._fetch(address, chain)
        self._sleep(0.35)
        if got is None:
            return entry
        got = dict(got)
        got["checked_at"] = self._now().isoformat()
        self._table[k] = got
        self._save()
        return got

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._table, indent=2, sort_keys=True))
