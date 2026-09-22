"""A bounded detector budget is spent on wallets that CAN be the deliverable.

The mission's own rule: the owner copy-trades on Hyperliquid, so an address HL
has never heard of cannot be the answer today. Measured 2026-09-22, the
shared-agent index covered 120 wallets and missed 6 of the 13 roster leads that
actually hold HL accounts, while spending part of the budget on addresses with
no HL account at all — and a shared agent is the one vector strong enough to
CONFIRM a wallet alone.

A wallet with no HL account is NOT dropped: it keeps its place after the ones
that have accounts, because opening an account is exactly the move this project
is watching for.
"""
from src import roster


def row(addr, tier="POSSIBLE", role="missing"):
    """`role` mirrors hl_identity: "user" = HL knows it, "missing" = a good read
    said it does not exist, None = the read failed."""
    return {"wallet": addr, "tier": tier, "is_service": False,
            "evidence": {"hl_role": role}}


def test_hl_accounts_come_first_within_the_cap():
    doc = {"wallets": [row("0xa"), row("0xb", role="user"), row("0xc"),
                       row("0xd", role="user")]}
    picked = roster.detector_candidates({}, doc, limit=2)
    assert picked == ["0xb", "0xd"]


def test_roster_order_is_kept_inside_each_group():
    doc = {"wallets": [row("0xb", role="user"), row("0xd", role="user"),
                       row("0xa"), row("0xc")]}
    assert roster.detector_candidates({}, doc, limit=4) == ["0xb", "0xd", "0xa", "0xc"]


def test_a_wallet_with_no_hl_account_is_deferred_not_dropped():
    doc = {"wallets": [row("0xa"), row("0xb", role="user")]}
    assert roster.detector_candidates({}, doc, limit=9) == ["0xb", "0xa"]


def test_an_unreadable_role_ranks_with_the_hl_accounts():
    """Rule 5: 'we could not tell' is not 'there is nothing there'. A failed
    read must not quietly demote a wallet out of every detector's budget."""
    doc = {"wallets": [row("0xa", role="missing"), row("0xb", role=None)]}
    assert roster.detector_candidates({}, doc, limit=9) == ["0xb", "0xa"]


def test_pinned_wallets_still_lead_and_are_never_trimmed():
    cfg = {"watch_wallets": ["0xPIN"], "known_self_wallets": []}
    doc = {"wallets": [row("0xb", role="user")]}
    picked = roster.detector_candidates(cfg, doc, limit=1)
    assert picked[0] == "0xpin"


def test_services_and_infrastructure_are_still_excluded():
    doc = {"wallets": [{"wallet": "0xs", "tier": "INFRASTRUCTURE", "evidence": {"hl_role": "user"}},
                       {"wallet": "0xt", "tier": "POSSIBLE", "is_service": True,
                        "evidence": {"hl_role": "user"}},
                       row("0xu", role="user")]}
    assert roster.detector_candidates({}, doc, limit=9) == ["0xu"]
