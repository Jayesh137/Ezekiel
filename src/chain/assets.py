"""What a token is worth, and what to do when we do not know.

The rule that matters is at the bottom of `value_usd`: an unknown price returns
None, never 0.0. A price-source outage that books a $2,000,000 ETH transfer as
zero does not raise an error anywhere — it silently drops the transfer below
every value threshold in the system, and the migration it represents is never
looked at again. None is loud; zero is invisible.
"""

import json
from pathlib import Path

# Dollar-pegged. Valued at par, so the overwhelming majority of observed flow
# never needs a price source at all.
STABLES = {
    "USDC", "USDC.E", "USDT", "USDT0", "DAI", "USDE", "SUSDE", "FRAX",
    "USDBC", "TUSD", "USDD", "FDUSD", "LUSD", "USDS",
}

# Symbol -> price-source id. Only assets we are willing to value.
MAJORS = {
    "ETH": "ethereum",
    "WETH": "ethereum",
    "WBTC": "wrapped-bitcoin",
    "CBBTC": "coinbase-wrapped-btc",
    "ARB": "arbitrum",
    "OP": "optimism",
    "BNB": "binancecoin",
    "POL": "matic-network",
    "MATIC": "matic-network",
    "WSTETH": "wrapped-steth",
    "WEETH": "wrapped-eeth",
}

DEFAULT_DECIMALS = 18


def decimals_of(row: dict, kind: str) -> int:
    """Token decimals for a raw Etherscan row.

    Native and internal transfers are always wei. ERC-20 rows carry their own
    `tokenDecimal`; a malformed one falls back to 18 rather than raising, since
    a bad decimal on one row must not abort a whole sweep.
    """
    if kind in ("native", "internal"):
        return DEFAULT_DECIMALS
    try:
        return int(row.get("tokenDecimal"))
    except (TypeError, ValueError):
        return DEFAULT_DECIMALS


def value_usd(symbol: str, amount: float, date_str: str,
              price_lookup) -> tuple[float | None, str]:
    """USD value of `amount` of `symbol` on `date_str`, and the basis used.

    Returns (None, "unpriced") for anything we cannot value — never (0.0, ...).
    """
    sym = (symbol or "").strip().upper()
    if sym in STABLES:
        return round(float(amount), 2), "stable_par"
    if sym in MAJORS:
        price = price_lookup(sym, date_str)
        if price is None:
            return None, "unpriced"
        return round(float(amount) * float(price), 2), "daily_close"
    return None, "unpriced"


class PriceCache:
    """Daily closes on disk, one file per symbol, fetched at most once ever.

    A recorded miss is cached too. Without that, every run re-requests the same
    unavailable date for as long as the record exists.
    """

    def __init__(self, directory: Path, fetch=None):
        self.directory = Path(directory)
        self._fetch = fetch
        self._loaded: dict[str, dict] = {}

    def _path(self, symbol: str) -> Path:
        return self.directory / f"{symbol}.json"

    def _table(self, symbol: str) -> dict:
        if symbol not in self._loaded:
            path = self._path(symbol)
            try:
                self._loaded[symbol] = json.loads(path.read_text())
            except (OSError, ValueError):
                self._loaded[symbol] = {}
        return self._loaded[symbol]

    def get(self, symbol: str, date_str: str) -> float | None:
        table = self._table(symbol)
        if date_str in table:
            value = table[date_str]
            return None if value is None else float(value)
        if self._fetch is None:
            return None
        price = self._fetch(symbol, date_str)
        table[date_str] = price
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(symbol).write_text(json.dumps(table, indent=2, sort_keys=True))
        return price
