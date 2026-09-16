# tests/test_roster_linkage_source.py
"""Linkage is a permanent fact and must not need a graph node to be expressible.

Every other vector in `build_roster` reads its own detector's file: correlation
from `correlations/`, shared agent from `agent_links/`, dormancy from
`dormancy/`, HL-native from `hl_transfers/`, behavioural from `candidates/`, an
explicit link from `identity/`. **Linkage alone had no source of its own** — it
was readable only off `transfer_graph/nodes[].evidence`, so a wallet that was
not a graph node could not carry it however true it was.

That coupling cost a lead. `0x5b5d5120…` reached the target at depth 1 through
an inferred CORRELATION edge and nothing else. When its amount-correlation
decayed below `min_confidence` (0.634 → 0.5513 → gone at 2026-09-15 06:31) the
edge went with it, the wallet stopped being reachable from the target, and the
roster lost `linkage` and `transfer` in the same run as `correlation`:
PROBABLE → WATCH with **zero** vectors.

The shared funder had not changed and has not changed since. Both the target
and that wallet were first funded by `0xf92402bb…`, and it still says so in
`data/labels/first_funders.json`. A permanent fact was being stored in a
volatile container.

This inverts the roster's whole premise. Tiers reward INDEPENDENT vectors
agreeing, and `_read`'s own docstring says "the point of five vectors is that
four still say something" — but one detector lapsing silently deleted another
detector's finding. Two vectors that can fail together were never two vectors.
"""


from src import roster

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
FUNDER = "0xf92402bb795fd7cd08fb83839689db79099c8c9c"
SHARER = "0x5b5d51203a0f9079f8aeb098a6523a13f298c060"
STRANGER = "0x2222222222222222222222222222222222222222"
EXCHANGE = "0x3333333333333333333333333333333333333333"


def test_shared_first_funder_is_found_without_a_graph_node():
    """The wallet need not be reachable in the graph for the fact to hold."""
    funders = {TARGET: FUNDER, SHARER: FUNDER, STRANGER: "0x9999999999999999999999999999999999999999"}

    found = roster.linkage_from_first_funders(funders, TARGET, excluded=set())

    assert found == {SHARER: FUNDER}


def test_a_wallet_first_funded_by_the_target_counts():
    """`linkage.evaluate` rates this higher than a shared funder, not lower."""
    funders = {TARGET: FUNDER, SHARER: TARGET}

    found = roster.linkage_from_first_funders(funders, TARGET, excluded=set())

    assert SHARER in found


def test_an_excluded_funder_links_nobody():
    """Rule 9: matching on shared exchange infrastructure identifies no one.

    Two Binance hot wallets with 15.8M and 30.5M transactions are already the
    reason `not_gcr` exists. A funder on the excluded list would otherwise hand
    a linkage vector to every wallet an exchange ever paid out to.
    """
    funders = {TARGET: EXCHANGE, SHARER: EXCHANGE}

    found = roster.linkage_from_first_funders(funders, TARGET, excluded={EXCHANGE})

    assert found == {}


def test_an_unknown_target_funder_links_nobody():
    """A missing funder is "we could not tell", never a match (rule 5).

    Without this, every wallet whose funder is also unresolved would match the
    target on None == None and the vector would fire across the whole cache.
    """
    funders = {SHARER: None, STRANGER: None}

    found = roster.linkage_from_first_funders(funders, TARGET, excluded=set())

    assert found == {}


def test_the_target_is_not_linked_to_himself():
    funders = {TARGET: FUNDER, SHARER: FUNDER}

    assert TARGET not in roster.linkage_from_first_funders(funders, TARGET, excluded=set())


def test_matching_is_case_insensitive():
    """The cache is written lowercased; Etherscan returns checksummed."""
    funders = {TARGET.upper(): FUNDER.upper(), SHARER.upper(): FUNDER}

    found = roster.linkage_from_first_funders(funders, TARGET, excluded=set())

    assert list(found) == [SHARER]
