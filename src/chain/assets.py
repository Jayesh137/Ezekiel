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

# Deliberately NOT extended for the dollar tokens recovered on 2026-09-16
# (Aave aUSDC, Axelar axlUSDC, GHO, PYUSD). Adding a ticker here prices ANY
# contract reporting it at par on every chain with no registry row, which is
# rule 2's own failure mode — $3.07B of counterfeit value — reintroduced by
# the fix for rule 2's opposite direction. Par for those is keyed on the
# verified CONTRACT instead; see load_par_contracts.

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
# A token wearing its chain's OWN native ticker from a contract is a forgery:
# native ETH has no contract on Ethereum, Arbitrum, Base or Optimism, so an
# ERC-20 calling itself "ETH" there is a poisoner's token priced at ETH's close.
# Measured 2026-09-16: 25 such contracts, ~$30.5M booked as real, each with a
# single sender — one of them "paid" $5.26M from `0xf078969e…` to
# `0x8579b784…0eb68e`, a vanity look-alike of his own deposit address.
#
# "native" is the only genuine form, so every contract is an impostor under
# `is_impostor`, and a native record (no contract) never is. Optimism's legacy
# OVM ETH predeploy really did move ETH as a token before Bedrock.
NATIVE_ONLY = "native"
CANONICAL_CONTRACTS: dict[tuple[str, str], set] = {
    ("ethereum", "ETH"): {NATIVE_ONLY},
    ("arbitrum", "ETH"): {NATIVE_ONLY},
    ("base", "ETH"): {NATIVE_ONLY},
    ("optimism", "ETH"): {NATIVE_ONLY, "0xdeaddeaddeaddeaddeaddeaddeaddeaddead0000"},
}


def load_canonical_contracts(config: dict | None = None,
                             registry_path=None) -> dict:
    """Canonical token contracts, from config plus an optional label file."""
    import json as _json

    # A copy of every SET, not just the dict: a registry row naming a key the
    # defaults already carry would otherwise add to the module constant itself.
    out = {key: set(addrs) for key, addrs in CANONICAL_CONTRACTS.items()}
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


def load_par_contracts(registry_path) -> set[str]:
    """Contracts the registry declares dollar-par, lowercased.

    A row is par only when it says `"par": true`. Existing rows are protective
    entries — they name the one real contract for a ticker so a forgery can be
    caught — and must not become pricing instructions by merely existing.

    This is rule 2 stated affirmatively: par attaches to the contract, which is
    the token's identity, never to the ticker, which the sender chooses.
    """
    import json as _json

    try:
        with open(registry_path) as f:
            rows = _json.load(f).get("tokens", [])
    except (OSError, ValueError, AttributeError):
        return set()
    out = set()
    for row in rows:
        if not row.get("par"):
            continue
        addrs = row.get("contracts") or (
            [row["contract"]] if row.get("contract") else [])
        out |= {str(a).lower() for a in addrs if a}
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


# Homoglyphs a token's own `tokenSymbol` uses where a registry entry has ASCII.
# Added only as they are MEASURED, never speculatively: a wrong fold here maps
# a forgery onto a genuine ticker, which is rule 2 pointed the expensive way.
#
# U+20AE, TUGRIK SIGN, is what Etherscan returns for Tether: the symbol is
# `USD₮0`, not `USDT0`. `.upper()` does not fold it, so the registry entry —
# which is correct — never matched, and 97,662 records of real Tether were
# quarantined as `unpriced_token` and dropped from the substrate by one glyph.
SYMBOL_HOMOGLYPHS = {"₮": "T"}


def normalise_symbol(symbol: str) -> str:
    """A token's reported ticker, folded to the form the registries use.

    NFKC first, which settles fullwidth and other compatibility forms, then the
    measured homoglyph map, then the strip/upper every lookup here already did.
    """
    import unicodedata

    s = unicodedata.normalize("NFKC", symbol or "")
    for bad, good in SYMBOL_HOMOGLYPHS.items():
        s = s.replace(bad, good)
    return s.strip().upper()


def canonical_symbol(contract: str | None, chain: str | None,
                     canonical: dict | None) -> str | None:
    """The symbol the registry files this CONTRACT under, or None.

    The affirmative half of rule 2. `is_impostor` has always used this registry
    to reject a token wearing a known ticker from the wrong contract; nothing
    used it to accept a genuine contract reporting an unexpected ticker. Aave's
    aToken calls itself `aArbUSDCn`, which is in no ticker registry and never
    will be, so 33,245 of its records were dropped although the contract is
    catalogued and verified.

    Called only after the ticker path has failed, so the scan costs nothing on
    the hot USDC path and runs only for a token that was about to be discarded.
    """
    if not canonical or not contract or not chain:
        return None
    c = contract.lower()
    ch = chain.lower()
    for (row_chain, row_symbol), known in canonical.items():
        if row_chain != ch:
            continue
        if isinstance(known, str):
            known = {known}
        if c in {k.lower() for k in known}:
            return row_symbol
    return None



# Cyrillic and Greek letters that are visually identical to Latin ones. Used
# ONLY to detect a disguise, never to price: folding `USDС` (Cyrillic С) onto
# `USDC` for pricing would book a counterfeit at par, which is rule 2's $3.07B
# lesson. See SYMBOL_HOMOGLYPHS above for the pricing side, which stays a
# one-entry measured map for exactly that reason.
_CONFUSABLES = {
    "Ѕ": "S", "С": "C", "А": "A", "Е": "E", "О": "O",
    "Р": "P", "Т": "T", "В": "B", "Н": "H", "М": "M",
    "К": "K", "Х": "X", "І": "I", "Ј": "J", "а": "A",
    "с": "C", "е": "E", "о": "O", "р": "P", "х": "X",
    "у": "Y", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Χ": "X",
}


def confusable_fold(symbol: str) -> str:
    """A ticker with every disguise stripped. For DETECTION only.

    NFKD then drop combining marks (so `ÚSDС` loses its acute and `USḌC` its
    dot), map look-alike Cyrillic/Greek letters to Latin, and discard anything
    that is not alphanumeric — which removes the invisible formatting
    characters a forger pads a symbol with, such as the U+180E in `Е᠎T᠎Н`.

    Never use this to price. `value_usd` deliberately does not, and
    tests/test_pricing_by_contract.py pins that the Cyrillic USDC must not fold
    there.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", symbol or "")
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in decomposed
                     if not unicodedata.combining(ch))
    return "".join(ch for ch in mapped if ch.isalnum()).upper()


def is_symbol_forgery(symbol: str, contract: str | None, chain: str | None,
                      canonical: dict | None) -> bool:
    """Is this token DISGUISED as one we price, from a contract that is not it?

    Two conditions, and the pair is what keeps it tight:

      * the symbol folds onto a ticker we price, and
      * it is not already that ticker — a token that simply IS `USDC` folds
        onto `USDC` too, and is not a forgery.

    Equality, never containment. `aUSDC`, `gtUSDC`, `variableDebtEthUSDC` and
    `yDAI+yUSDC+yUSDT+yTUSD` are real Aave, Morpho and Yearn tokens that merely
    contain a ticker; an earlier substring version of this check flagged every
    one of them on live data.

    Silent without a registry, exactly as `is_impostor` is: unable to tell a
    counterfeit from a token we have not catalogued, and guessing in that
    direction discards real transfers.
    """
    if not canonical:
        return False
    plain = normalise_symbol(symbol)
    if plain in STABLES or plain in MAJORS:
        return False                      # it is the real ticker, priced above
    folded = confusable_fold(symbol)
    if folded not in STABLES and folded not in MAJORS:
        return False                      # not pretending to be anything
    known = canonical.get(((chain or "").lower(), folded))
    if known:
        if isinstance(known, str):
            known = {known}
        if (contract or "").lower() in {k.lower() for k in known}:
            return False                  # genuinely that token, oddly labelled
    return True

def value_usd(symbol: str, amount: float, date_str: str,
              price_lookup, *, contract: str | None = None,
              chain: str | None = None,
              canonical: dict | None = None,
              par_contracts: set | None = None) -> tuple[float | None, str]:
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
    sym = normalise_symbol(symbol)
    if is_impostor(sym, contract, chain, canonical):
        # A token wearing a stablecoin's ticker from the wrong contract. Not
        # "unpriced" (that means we do not price this token) and never a dollar
        # figure: it is a forgery, and saying so is the point.
        return None, "impostor_token"
    priced = _price_as(sym, amount, date_str, price_lookup)
    if priced is not None:
        return priced

    # The ticker is unknown, so ask what this CONTRACT is before discarding it.
    # Only a registry-VERIFIED contract gets a second chance — an unknown token
    # is still `unpriced`, because pricing one at par on the strength of a
    # plausible ticker is how $3.07B of counterfeit value was booked as real.
    c = (contract or "").lower()
    if c and par_contracts and c in {a.lower() for a in par_contracts}:
        return round(float(amount), 2), "stable_par"
    known = canonical_symbol(contract, chain, canonical)
    if known and known != sym:
        priced = _price_as(known, amount, date_str, price_lookup)
        if priced is not None:
            return priced
    return None, "unpriced"


def _price_as(sym: str, amount: float, date_str: str,
              price_lookup) -> tuple[float | None, str] | None:
    """Value `amount` as `sym`, or None if `sym` is not a token we price."""
    if sym in STABLES:
        return round(float(amount), 2), "stable_par"
    if sym in MAJORS:
        price = price_lookup(sym, date_str)
        if price is None:
            return None, "price_unavailable"
        return round(float(amount) * float(price), 2), "daily_close"
    return None


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
