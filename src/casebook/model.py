"""The casebook's likelihood model (spec 2026-10-08 §7). Data; score.py applies it.

Each entry is a log10 likelihood-ratio band (low, mid, high): how much more
likely the observation is if the wallet is his than if it is not. `basis` says
where the number came from: "measured" (a calibrated panel), "estimated" (a
count from this project's own data), "assumed" (declared here with its reason)
or "operator" (a ruling). The index carries this table verbatim, so a reader a
year from now sees exactly what produced each rank.

Change it only by bumping MODEL_VERSION and recording why in
docs/incident-log.md, and never to move one wallet (rule 4: the recall check in
score.calibration is reported, never tuned to).
"""

from __future__ import annotations

import math

MODEL_VERSION = "casebook-model/2026-10-08.1"

# 1 : 1,000 for a wallet in the casebook before its own evidence is read: the
# casebook holds O(10^3) suspects and he plausibly has O(1) unknown wallets
# among them. The order never depends on it; the probability column does.
PRIOR_LOG10_ODDS = -3.0

FAMILY_CATEGORY = {
    "control": "protocol",
    "association": "protocol",
    "money": "financial",
    "infrastructure": "financial",
    "gap": "financial",
    "tooling": "behaviour",
    "behaviour": "behaviour",
    "coactivity": "behaviour",
    "lifecycle": "successor",
    "ruling": "ruling",
}
FAMILY_CLIP = {"control": (-3.0, 5.0), "ruling": (-3.0, 0.0)}
DEFAULT_CLIP = (-2.0, 2.0)

# Inside one category the strongest family counts in full and each other one at
# this weight: two kinds of money link are not two independent votes (§7.3).
SECONDARY_WEIGHT = 0.5
# A lapsed item (seen live, then gone 24 h or more, cause unknown) counts at this
# share of its mid band in the central estimate (§6.3).
LAPSED_WEIGHT = 0.5

# status -> weight of the item's (low, mid, high) in the (now, central, ceiling) bands
STATUS_WEIGHTS = {
    "current": (1.0, 1.0, 1.0),
    "standing": (1.0, 1.0, 1.0),
    "lapsed": (0.0, LAPSED_WEIGHT, 1.0),
    "refuted": (0.0, 0.0, 1.0),
    "historical": (0.0, 0.0, 1.0),
    "invalidated": (0.0, 0.0, 0.0),
}
STATUSES = tuple(STATUS_WEIGHTS)

# Protocol facts: an approval, a sub-account or a declared code happened and
# cannot un-happen, so a lapse leaves them standing.
STANDING_KINDS = frozenset({"subaccount_of_cluster", "agent_of_cluster", "staking_link",
                            "shared_agent", "referral_with_cluster"})

_DIRECT_WHY = ("2 of the 412 L1 counterparties (>= $50K) of his three config wallets are his "
               "(measured 2026-10-06); OTC desks, market makers and exchanges dominate the rest "
               "(rule 8: a transfer is not ownership).")


def _k(family, label, band, basis, why):
    return {"family": family, "label": label, "band": band, "basis": basis, "why": why}


KINDS = {
    # Protocol control
    "subaccount_of_cluster": _k("control", "Sub-account link with one of his wallets", (3.0, 4.0, 5.0),
                                "assumed", "A sub-account exists only because its master created it: "
                                "control declared by the protocol (CLAUDE.md: confirms alone)."),
    "agent_of_cluster": _k("control", "Agent link with one of his wallets", (3.0, 4.0, 5.0), "assumed",
                           "An agent is authorised by the account it trades for: control declared by "
                           "the protocol."),
    "staking_link": _k("control", "Staking link with one of his wallets", (2.0, 3.0, 4.0), "assumed",
                       "A staking link is declared by both accounts."),
    "shared_agent": _k("control", "Shares an authorised agent with him", (2.0, 3.0, 4.0), "assumed",
                       "One agent key authorised by two accounts is one operator unless a service "
                       "shares keys."),
    # Money with his wallets
    "direct_transfer": _k("money", "Observed transfer with his wallets", (0.3, 0.7, 1.3), "estimated",
                          _DIRECT_WHY),
    "circle_flow": _k("money", "Circle transfer with his wallets", (0.3, 0.7, 1.3), "estimated",
                      "A Circle transfer named by the protocol on both ends; the direct-transfer base "
                      "rate. " + _DIRECT_WHY),
    "boundary_transfer": _k("money", "Bridge record of money with his world", (0.3, 0.7, 1.3),
                            "estimated", "A Bridge2/Circle record naming his wallet on one side; the "
                            "direct-transfer base rate. " + _DIRECT_WHY),
    "two_way_flow": _k("money", "Money both ways with him", (0.7, 1.0, 1.7), "assumed",
                       "A relationship: his own wallets have one, and so do OTC desks."),
    "funded_by_target": _k("money", "First funding or gas from the target", (0.7, 1.3, 2.0), "assumed",
                           "Gas or first funding of a fresh wallet from his is the shape of funding "
                           "himself."),
    "boundary_member": _k("money", "Holds money that passed through his world", (0.0, 0.3, 0.7),
                          "assumed", "Weak: an address holding his money, active on Hyperliquid."),
    "trace_reach": _k("money", "His money reached it through quiet hops", (0.0, 0.3, 0.7), "assumed",
                      "Reach is evidence, never a vote."),
    # Shared private infrastructure
    "private_deposit_address": _k("infrastructure", "Paid his private deposit address", (0.7, 1.5, 2.0),
                                  "assumed", "A CEX deposit address belongs to one exchange account "
                                  "(address reuse is the strongest single linkage signal); an OTC "
                                  "payer is the alternative."),
    "boundary_deposit_address": _k("infrastructure", "Paid his deposit address (protocol record)",
                                   (0.7, 1.3, 2.0), "assumed", "The same argument, read from Bridge2 "
                                   "or Circle's own record."),
    "hl_deposit_address": _k("infrastructure", "Paid his Hyperliquid deposit address", (0.7, 1.3, 2.0),
                             "assumed", "The deposit-address argument inside Hyperliquid."),
    "quiet_payee": _k("infrastructure", "Paid a quiet wallet he also paid", (0.3, 0.7, 1.3), "assumed",
                      "Shared quiet infrastructure; rule 9 already applied upstream."),
    "quiet_first_funder": _k("infrastructure", "Shares his first funder", (0.3, 0.7, 1.3), "assumed",
                             "A first funder measured quiet (rule 9); invalidated if the "
                             "whole chain later measures it busy."),
    "linkage_graph": _k("infrastructure", "Shares a deposit address or funder with him",
                        (0.3, 0.7, 1.3), "assumed", "Shared infrastructure in the transfer graph."),
    # Custody gap
    "amount_correlation": _k("gap", "His exit re-appeared as its deposit", "confidence", "assumed",
                             "Every correlation lead so far was a bot or a coincidence, so the band "
                             "is wide and scales with the correlator's confidence."),
    # Lifecycle
    "dormancy_handoff": _k("lifecycle", "Born inside a silence of his", "score", "assumed",
                           "About 9% of accounts born during his history would land in one of his "
                           "anomalous windows by chance (7 gaps x 3 days of ~240 days)."),
    # Tooling
    "study_tooling": _k("tooling", "Makes orders the way he does (candidate study)", "study", "measured",
                        "The candidate study's calibrated likelihood ratio (Clopper-Pearson bound, "
                        "spec 2026-10-06 section 8.3)."),
    "execution_program": _k("tooling", "Reproduces his per-coin clip table", (0.7, 1.3, 2.0), "assumed",
                            "Census-gated match of his SDK slicer; superseded by the study's verdict "
                            "where it exists (same mechanism)."),
    # Behaviour
    "behavioural_vote": _k("behaviour", "Trades like him (validated scorer)", (0.0, 0.3, 0.7), "assumed",
                           "The behavioural scorer votes only once validated (rule 4)."),
    "style_veto": _k("behaviour", "Style veto: a different human", (-1.0, -0.5, -0.2), "assumed",
                     "A style veto is a positive finding of a different human."),
    # Association
    "referral_with_cluster": _k("association", "Joined to his wallet by a quiet referral code",
                                (0.3, 0.7, 1.3), "assumed",
                                "A code is chosen by whoever types it: an association, quiet codes "
                                "only."),
    # Co-activity
    "comovement": _k("coactivity", "Trade timing against his", "comovement", "assumed",
                     "Leads or ties him (same_hand) or follows him (copier): the one timing reading "
                     "a copier cannot fake."),
    # Operator
    "operator_not_him": _k("ruling", "Operator ruling: not him", (-3.0, -3.0, -2.0), "operator",
                           "config.casebook_rulings."),
    # Context: recorded for the reader, never scored
    "portfolio_overlap": _k("context", "Holds his basket", (0.0, 0.0, 0.0), "context",
                            "A copier holds the same basket by definition."),
    "graph_reach": _k("context", "Reached by the transfer graph", (0.0, 0.0, 0.0), "context",
                      "Reach without direct flow is not a vote."),
    "operator_group": _k("context", "Member of a sub-account family", (0.0, 0.0, 0.0), "context",
                         "Joins cases into one operator cluster."),
    "referral_pair": _k("context", "Shares a quiet referral code", (0.0, 0.0, 0.0), "context",
                        "Related, not merged: an association is not control."),
    "behavioural_score": _k("context", "Behavioural score (no vote)", (0.0, 0.0, 0.0), "context",
                            "An unvalidated score is history, not evidence."),
    "hyperevm_nonce": _k("context", "HyperEVM nonce", (0.0, 0.0, 0.0), "context",
                         "Activity on HyperEVM."),
    "study_context": _k("context", "Candidate study reading", (0.0, 0.0, 0.0), "context",
                        "A study verdict that judged nothing (neutral, uncalibrated, insufficient)."),
}
CONTEXT_KINDS = frozenset(k for k, spec in KINDS.items() if spec["family"] == "context")

_SCALED = {
    "confidence": "confidence c: log10(1+2c) / log10(1+9c) / log10(1+30c)",
    "score": "handoff score s: log10(1+2s) / log10(1+9s) / log10(1+20s)",
    "study": "study LR: log10 LR / log10 LR / log10 LR + 0.3 (for), half log10 LR (against)",
    "comovement": "same_hand 0 / 0.2 / 0.5; copier -0.7 / -0.4 / 0; otherwise 0",
}
_ZERO = (0.0, 0.0, 0.0)


def _unit(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def _round(values):
    return tuple(round(v, 4) for v in values)


def band(kind: str, strength=None, facts=None) -> tuple[float, float, float]:
    """The (low, mid, high) log10 likelihood ratio of one item. Pure.

    A scaled kind with no reading weighs nothing (rule 6): it is not evidence of
    absence and not a zero-strength match either.
    """
    spec = KINDS.get(kind)
    if spec is None or spec["family"] == "context":
        return _ZERO
    shape = spec["band"]
    if isinstance(shape, tuple):
        return shape
    facts = facts if isinstance(facts, dict) else {}
    if shape in ("confidence", "score"):
        x = _unit(strength)
        if x is None:
            return _ZERO
        top = 30.0 if shape == "confidence" else 20.0
        return _round((math.log10(1 + 2 * x), math.log10(1 + 9 * x), math.log10(1 + top * x)))
    if shape == "study":
        lr = facts.get("lr")
        verdict = facts.get("verdict")
        if isinstance(lr, bool) or not isinstance(lr, (int, float)) or not math.isfinite(lr) or lr <= 0:
            return _ZERO
        v = math.log10(lr)
        if verdict in ("for", "mixed") and v > 0:
            return _round((v, v, v + 0.3))
        if verdict in ("against", "mixed") and v < 0:
            return _round((v, v, v * 0.5))
        return _ZERO
    if shape == "comovement":
        return {"same_hand": (0.0, 0.2, 0.5), "copier": (-0.7, -0.4, 0.0)}.get(facts.get("verdict"), _ZERO)
    return _ZERO


def describe() -> dict:
    """The whole model as JSON, for the index (a reader in a year needs no code)."""
    kinds = {}
    for kind, spec in KINDS.items():
        shape = spec["band"]
        kinds[kind] = {"family": spec["family"], "label": spec["label"], "basis": spec["basis"],
                       "why": spec["why"],
                       "band": list(shape) if isinstance(shape, tuple) else _SCALED[shape]}
    return {"version": MODEL_VERSION, "prior_log10_odds": PRIOR_LOG10_ODDS,
            "categories": dict(FAMILY_CATEGORY),
            "clips": {**{f: list(c) for f, c in FAMILY_CLIP.items()}, "default": list(DEFAULT_CLIP)},
            "secondary_weight": SECONDARY_WEIGHT,
            "status_weights": {s: list(w) for s, w in STATUS_WEIGHTS.items()},
            "standing_kinds": sorted(STANDING_KINDS), "kinds": kinds,
            "reading": ("log10 posterior odds = prior + for each category: strongest family + "
                        "half of every other family; a family is the max of its supports, the min "
                        "of its againsts, or the larger in magnitude when both. probability = "
                        "1 / (1 + 10^-odds). now = current and standing items at low; central = "
                        "current/standing at mid plus lapsed at half mid; ceiling = everything "
                        "not invalidated at high.")}
