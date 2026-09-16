# tests/test_symbol_forgery.py
"""A confusable fold is wrong for PRICING and right for DETECTION.

Keeping unpriced records (2026-09-16) opened a hole. Address-poisoning has
always been caught — `forged_side` compares addresses. Symbol poisoning was
not, because it never had to be: a token calling itself `UЅDС` with a Cyrillic
Ѕ and С is in no registry, so it priced as `unpriced` and was quarantined for
that reason. Once unpriced records became worth keeping, the forgeries came
with them.

Measured on the live substrate straight after the first targeted re-sweeps:
**314 records across 11 forged symbols** — `USDС`, `UЅDС`, `ÚSDС`, `UЅDC`,
`ÚЅDС`, `USḌC`, `EТH`, `ЕТН`, `UЅDT`, `WBТC` — every one a homoglyph of a
ticker we price. They carry `amount_usd: None`, so rule 2's money protection
held and no dollar figure was inflated. But they become graph EDGES, linking a
wallet to whoever sent the poison.

The resolution is the same signal pointed two ways, and each direction is only
safe because the other exists:

  * **Pricing must never fold confusables.** `USDС` folded onto `USDC` would be
    priced at par — a counterfeit booked as real money, which is the $3.07B
    lesson. `SYMBOL_HOMOGLYPHS` therefore stays a measured map with one entry
    (U+20AE, Tether's own `USD₮0`), and tests/test_pricing_by_contract.py pins
    that the Cyrillic case must NOT fold.
  * **Detection must fold aggressively.** A symbol that folds onto a ticker we
    price, while not BEING that ticker, is disguised on purpose. Nothing else
    explains it.

The two conditions together are what keep it tight. `aUSDC`, `gtUSDC`,
`variableDebtEthUSDC` and `yDAI+yUSDC+yUSDT+yTUSD` are real Aave, Morpho and
Yearn tokens that merely CONTAIN a ticker; they fold to themselves, not to
`USDC`, and must keep flowing. An earlier substring-matching version of this
check flagged all of them, which is why equality is the test.
"""

import pytest

from src.chain import assets
from src.chain import spam as spam_mod

ARB_USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
CANONICAL = {("arbitrum", "USDC"): ARB_USDC}
FAKE = "0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


# --- the fold itself -------------------------------------------------------

@pytest.mark.parametrize("forged", ["USDС", "UЅDС", "ÚSDС", "UЅDC", "ÚЅDС", "USḌC"])
def test_a_disguised_usdc_folds_onto_usdc(forged):
    assert assets.confusable_fold(forged) == "USDC"


@pytest.mark.parametrize("forged,real", [("EТH", "ETH"), ("UЅDT", "USDT"),
                                         ("WBТC", "WBTC")])
def test_other_disguised_majors_fold_too(forged, real):
    assert assets.confusable_fold(forged) == real


@pytest.mark.parametrize("genuine", ["aUSDC", "gtUSDC", "variableDebtEthUSDC",
                                     "yDAI+yUSDC+yUSDT+yTUSD", "vAMM-WETH/USDC"])
def test_a_real_defi_token_containing_a_ticker_does_not_fold_onto_it(genuine):
    """These are Aave, Morpho and Yearn tokens and must keep flowing.

    A substring version of this check flagged all of them on live data.
    """
    assert assets.confusable_fold(genuine) not in ("USDC", "USDT", "ETH")


# --- the classification ----------------------------------------------------

def test_a_homoglyph_ticker_is_a_symbol_forgery():
    assert assets.is_symbol_forgery("UЅDС", FAKE, "arbitrum", CANONICAL) is True


def test_the_real_token_is_not_a_forgery():
    assert assets.is_symbol_forgery("USDC", ARB_USDC, "arbitrum", CANONICAL) is False


def test_an_ordinary_unknown_token_is_not_a_forgery():
    """PEPE is not pretending to be anything. It stays, unpriced."""
    assert assets.is_symbol_forgery("PEPE", FAKE, "ethereum", CANONICAL) is False


def test_tethers_own_tugrik_ticker_is_not_a_forgery():
    """`USD₮0` IS Tether's symbol. The one homoglyph we price through."""
    assert assets.is_symbol_forgery(
        "USD₮0", "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        "arbitrum", CANONICAL) is False


# --- and the sweep acts on it ---------------------------------------------

def _rec(asset, contract=FAKE):
    return {"src": "0x1111111111111111111111111111111111111111",
            "dst": "0x2222222222222222222222222222222222222222",
            "asset": asset, "amount": 1000.0, "amount_usd": None,
            "value_basis": "unpriced", "token_address": contract,
            "chain": "arbitrum", "spam": False}


def test_a_symbol_forgery_is_quarantined():
    reason = spam_mod.classify_spam(_rec("UЅDС"), {}, wallet="0x1111111111111111111111111111111111111111",
                                    canonical=CANONICAL)

    assert reason == "symbol_forgery"


def test_an_ordinary_unpriced_token_is_still_kept():
    """The change this protects must not swallow what it was built for."""
    reason = spam_mod.classify_spam(_rec("PEPE"), {}, wallet="0x1111111111111111111111111111111111111111",
                                    canonical=CANONICAL)

    assert reason is None


def test_a_real_defi_token_is_still_kept():
    reason = spam_mod.classify_spam(_rec("variableDebtEthUSDC"), {},
                                    wallet="0x1111111111111111111111111111111111111111",
                                    canonical=CANONICAL)

    assert reason is None


def test_without_a_registry_nothing_is_called_a_forgery():
    """Silent about what it cannot know, exactly as is_impostor is."""
    assert spam_mod.classify_spam(_rec("UЅDС"), {}, wallet="0x1", canonical=None) is None


# --- cleaning up the ones already stored ----------------------------------

def test_the_quarantine_pass_marks_a_stored_symbol_forgery():
    """314 were already on disk before the check existed.

    They must be marked spam, or they stay graph edges for ever: nothing
    re-reads a stored record's classification except this pass.
    """
    from scripts.quarantine_impostor_tokens import scrub_records

    recs = [{"asset": "UЅDС", "token_address": FAKE, "chain": "arbitrum",
             "amount_usd": None, "value_basis": "unpriced", "spam": False}]

    marked, _ = scrub_records(recs, CANONICAL)

    assert marked == 1
    assert recs[0]["spam"] is True
    assert recs[0]["amount_usd"] is None      # never 0.0


def test_the_quarantine_pass_leaves_a_genuine_unpriced_token_alone():
    from scripts.quarantine_impostor_tokens import scrub_records

    recs = [{"asset": "PEPE", "token_address": FAKE, "chain": "ethereum",
             "amount_usd": None, "value_basis": "unpriced", "spam": False}]

    marked, _ = scrub_records(recs, CANONICAL)

    assert marked == 0
    assert recs[0]["spam"] is False
