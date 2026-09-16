# tests/test_unpriced_records_kept.py
"""A transfer we cannot value is still a transfer. Keep it, value it as None.

"We could not price it" was being treated as "it is not real". A record whose
`amount_usd` came back None was quarantined as `unpriced_token`, never written
to `data/transfers/`, and the cursor advanced past it — so the movement, the
two addresses and the link between them were destroyed because a price lookup
missed. Measured on the live ledger: **332,636 records**, of which only 14%
carry an advertising-shaped symbol.

That is rule 6 at the wrong end of the pipe. The rule says never price a
missing value as 0.0 because zero is invisible to every threshold; the same
reasoning says a missing value must not be grounds for deletion either. A PEPE
transfer between two addresses is an observed edge whether or not we know what
it was worth, and discovery — the only vector that reaches an address nobody
has seen — runs on edges, not on dollars.

**The contract this file pins:**

1. An unpriced record is STORED, with `amount_usd` None and `value_basis`
   "unpriced". Never 0.0.
2. It becomes a graph EDGE, so the two addresses are linked and reachable.
3. It can never SATISFY a value threshold. Unknown is not "big enough", and it
   is equally not "small enough to be dust" — those are different questions and
   the dust filter must not delete what it cannot measure.
4. A forgery is still destroyed. `impostor_token` yields a null value too, and
   admitting those would undo rule 2 — the $3.07B lesson — through the door
   opened for rule 6.

Point 4 is the trap. The old test for "drop this" was `amount_usd is None`,
which silently covered both the token we cannot value and the token we have
PROVEN is counterfeit. Splitting them is the whole work: the decision has to be
made on `value_basis`, which says WHY there is no number, not on the absence of
the number itself.
"""

from src.chain import spam as spam_mod
from src.transfer_graph import normalise_transfer_record

WALLET = "0x1111111111111111111111111111111111111111"
OTHER = "0x2222222222222222222222222222222222222222"
VOLUME = {OTHER: 5_000.0}


def _rec(**kw):
    base = {"src": WALLET, "dst": OTHER, "asset": "PEPE", "amount": 1000.0,
            "amount_usd": None, "value_basis": "unpriced", "ts": 1_700_000_000,
            "tx_hash": "0xabc", "chain": "ethereum", "spam": False}
    base.update(kw)
    return base


# --- 1. kept, and kept as None --------------------------------------------

def test_an_unpriced_token_is_not_quarantined():
    assert spam_mod.classify_spam(_rec(), VOLUME, wallet=WALLET) is None


def test_a_price_outage_on_a_major_is_still_not_quarantined():
    """The one case that was already right, which must stay right."""
    assert spam_mod.classify_spam(
        _rec(asset="ETH", value_basis="price_unavailable"),
        VOLUME, wallet=WALLET) is None


# --- 2. it reaches the graph ----------------------------------------------

def test_an_unpriced_record_becomes_an_edge():
    edge = normalise_transfer_record(_rec())

    assert edge is not None
    assert edge["src"] == WALLET and edge["dst"] == OTHER


def test_the_edge_carries_no_value_rather_than_zero():
    """Rule 6. A 0.0 here would read as a real transfer worth nothing."""
    edge = normalise_transfer_record(_rec())

    assert edge["amount_usd"] is None


# --- 3. unknown never satisfies a threshold, and is never dust ------------

def test_edge_passes_dust_keeps_an_unknown_value():
    """Dust means "measured, and tiny". Unknown is a different statement.

    Treating None as 0.0 here would delete from the graph exactly the records
    this change exists to keep — the saving undone one function later.
    """
    from src.transfer_graph import edge_passes_dust

    assert edge_passes_dust({"amount_usd": None}, dust_usd=1.0) is True


def test_edge_passes_dust_still_drops_measured_dust():
    from src.transfer_graph import edge_passes_dust

    assert edge_passes_dust({"amount_usd": 0.004}, dust_usd=1.0) is False


def test_edge_passes_dust_keeps_a_real_amount():
    from src.transfer_graph import edge_passes_dust

    assert edge_passes_dust({"amount_usd": 5_000.0}, dust_usd=1.0) is True


# --- 4. forgeries are still destroyed -------------------------------------

def test_an_impostor_token_is_still_quarantined():
    """Rule 2 must not be undone by the door opened for rule 6.

    A token wearing a stablecoin's ticker from the wrong contract has a null
    value for a completely different reason: not "we do not know", but "we know
    this is a forgery". Deciding on `value_basis` is what tells them apart.
    """
    reason = spam_mod.classify_spam(
        _rec(asset="USDC", value_basis="impostor_token"), VOLUME, wallet=WALLET)

    assert reason == "impostor_token"


def test_an_impostor_never_becomes_an_edge():
    assert normalise_transfer_record(
        _rec(asset="USDC", value_basis="impostor_token", spam=True)) is None


def test_a_zero_value_transfer_is_still_quarantined():
    assert spam_mod.classify_spam(_rec(amount=0.0), VOLUME, wallet=WALLET) == "zero_value"


def test_a_lookalike_is_still_quarantined():
    twin = "0x2222" + "9" * 32 + "2222"   # shares OTHER's first-4 and last-4
    volume = {OTHER: 5_000.0, twin: 1.0}
    reason = spam_mod.classify_spam(
        _rec(src=twin, dst=WALLET, amount_usd=10.0, value_basis="stable_par"),
        volume, wallet=WALLET)

    assert reason == "lookalike"


def test_measured_dust_is_still_quarantined():
    assert spam_mod.classify_spam(
        _rec(amount_usd=0.004, value_basis="stable_par"),
        VOLUME, wallet=WALLET) == "dust"


# --- 5. the graph knows more than the frontier walks ----------------------

def test_an_unvalued_edge_is_a_graph_edge_but_is_not_walkable():
    """A deliberate divergence between two filters that used to match exactly.

    Keeping the edge costs nothing and preserves a real link. WALKING it spends
    an Etherscan lookup, and the frontier sweep passes no price_lookup by
    design — so every frontier major returns `price_unavailable`, and making
    those walkable would aim the entire lookup budget at ETH counterparties.
    Measured when the graph gate opened: 326,886 -> 619,877 edges, 203,279 of
    the 277,208 newcomers ETH.

    The divergence is in the safe direction: the graph knows more than the
    frontier walks. The 2026-07-28 bug was the opposite, and far worse.
    """
    from src.transfer_graph import _expandable_edges, edge_passes_dust

    unvalued = {"src": WALLET, "dst": OTHER, "amount_usd": None,
                "discovery_source": "l1", "ts": 1}

    assert edge_passes_dust(unvalued, dust_usd=1.0) is True
    assert _expandable_edges([unvalued], dust_usd=1.0) == []


def test_a_valued_edge_is_still_walkable():
    """Guard against the divergence swallowing the frontier entirely."""
    from src.transfer_graph import _expandable_edges

    valued = {"src": WALLET, "dst": OTHER, "amount_usd": 5_000.0,
              "discovery_source": "l1", "ts": 1}

    assert _expandable_edges([valued], dust_usd=1.0) == [valued]
