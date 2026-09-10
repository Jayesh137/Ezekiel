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
#
# Verified live against CoinGecko on 2026-08-31 (see
# docs/superpowers/price-source-report.md). One entry was wrong: POL pointed
# at "matic-network", which CoinGecko's own API now reports as
# `{"symbol": "matic", "name": "MATIC (migrated to POL)"}` -- a deprecated
# legacy id, not the live POL token. `/search?query=POL` returns
# "polygon-ecosystem-token" (symbol POL, name "POL (ex-MATIC)") as the correct
# current id. MATIC maps to the legacy "matic-network" here, but that is only
# the PRE-migration half of the answer: which id is correct for MATIC depends on
# the date, so src/chain/prices.py resolves it per date via MIGRATED_COIN_IDS
# (pre-2024-09-04 keeps the legacy series; on or after it, MATIC is a
# legacy-symbol contract or bridged wrapper and prices off POL). The date
# conditional lives there rather than in this table on purpose. Every other id
# below was independently confirmed
# correct (ETH/WETH share "ethereum" on purpose -- WETH is arbitrage-pegged
# 1:1 to ETH, so pricing it off ETH's series is intentional, not an oversight).
MAJORS = {
    "ETH": "ethereum",
    "WETH": "ethereum",
    "WBTC": "wrapped-bitcoin",
    "CBBTC": "coinbase-wrapped-btc",
    "ARB": "arbitrum",
    "OP": "optimism",
    "BNB": "binancecoin",
    "POL": "polygon-ecosystem-token",
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


# (chain, SYMBOL) -> the contract(s) that really are that token there.
#
# A transfer is priced from its `tokenSymbol`, which the sender chooses. Anyone
# can deploy a token called "USDC" for a few cents, and address-poisoning kits
# do exactly that: measured 2026-09-10, 236 transfers of a token symboled USDC
# from contract 0xa38ae1ea... (unverified, flagged "poor reputation" by
# Arbiscan, 599 transfers in a single transaction) were booked at face value as
# $986,395,888 of real money, because nothing compared the contract.
#
# Deliberately small and only what can be verified from config. An entry that is
# WRONG would quarantine a real stablecoin transfer, which is worse than the bug
# it fixes, so unknown (chain, symbol) pairs stay unverified rather than guessed.
# Extend via data/labels/token_contracts.json.
CANONICAL_CONTRACTS: dict[tuple[str, str], str] = {}


def load_canonical_contracts(config: dict | None = None,
                             registry_path=None) -> dict:
    """Canonical token contracts, from config plus an optional label file."""
    import json as _json

    out = dict(CANONICAL_CONTRACTS)
    config = config or {}
    arb_usdc = (config.get("usdc_contract_arbitrum") or "").lower()
    if arb_usdc:
        out.setdefault(("arbitrum", "USDC"), set()).add(arb_usdc)
    if registry_path is not None:
        try:
            with open(registry_path) as f:
                for row in _json.load(f).get("tokens", []):
                    chain = (row.get("chain") or "").lower()
                    sym = (row.get("symbol") or "").strip().upper()
                    # One symbol can have several legitimate contracts. On
                    # Optimism BOTH native USDC (0x0b2C639c...) and bridged
                    # USDC.e (0x7F5c764c...) report symbol "USDC" on-chain — the
                    # bridged one was never renamed. Treating either as the only
                    # real one would quarantine the other's genuine transfers.
                    addrs = row.get("contracts") or (
                        [row["contract"]] if row.get("contract") else [])
                    addrs = {str(a).lower() for a in addrs if a}
                    if chain and sym and addrs:
                        out.setdefault((chain, sym), set()).update(addrs)
        except (OSError, ValueError, AttributeError):
            pass
    return out


def is_impostor(symbol: str, contract: str | None, chain: str | None,
                canonical: dict | None) -> bool:
    """True only when we KNOW this token's real contract and this is not it.

    Silent about everything else. Without a canonical entry we cannot tell a
    counterfeit from a token we simply have not catalogued, and guessing in that
    direction discards real transfers.
    """
    if not canonical or not contract or not chain:
        return False
    known = canonical.get((chain.lower(), (symbol or "").strip().upper()))
    if not known:
        return False
    # A str entry is one legitimate contract; a set is several, and a token
    # matching ANY of them is genuine.
    if isinstance(known, str):
        known = {known}
    return contract.lower() not in known


def value_usd(symbol: str, amount: float, date_str: str,
              price_lookup, *, contract: str | None = None,
              chain: str | None = None,
              canonical: dict | None = None) -> tuple[float | None, str]:
    """USD value of `amount` of `symbol` on `date_str`, and the basis used.

    Returns (None, "unpriced") for a token we do not price at all, and
    (None, "price_unavailable") for a known major whose price_lookup came back
    empty — never (0.0, ...) for either. The two must stay distinguishable: an
    unknown token is noise by design, but a major we simply could not price
    today (a price-source outage) is real money. Collapsing both into the same
    "unpriced" basis is what let a downstream spam filter quarantine a live
    ETH transfer on the strength of a price-source hiccup — the classifier
    could not tell "worthless" from "worth unknown right now."
    """
    sym = (symbol or "").strip().upper()
    if is_impostor(sym, contract, chain, canonical):
        # A token wearing a stablecoin's ticker from the wrong contract. Not
        # "unpriced" (that means we do not price this token) and never a dollar
        # figure: it is a forgery, and saying so is the point.
        return None, "impostor_token"
    if sym in STABLES:
        return round(float(amount), 2), "stable_par"
    if sym in MAJORS:
        price = price_lookup(sym, date_str)
        if price is None:
            return None, "price_unavailable"
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
                loaded = json.loads(path.read_text())
            except (OSError, ValueError):
                loaded = {}
            self._loaded[symbol] = loaded if isinstance(loaded, dict) else {}
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
