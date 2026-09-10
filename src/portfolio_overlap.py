# src/portfolio_overlap.py
"""Two wallets holding the same unusual basket, at the same moment.

Positions are a snapshot of conviction. Holding BTC short during a sell-off says
nothing — measured 2026-09-10, BTC is held by 53 of 147 eligible wallets and HYPE
by another 53. Holding `xyz:COST`, held by exactly one wallet in the whole
sample, in the same direction as the target at the same time is a different
statement entirely.

So overlap is weighted by RARITY throughout, reusing
`calibration.market_frequency` (Laplace-smoothed) rather than inventing a second
notion of rare. Without that weighting this vector would match every whale
running a broad book against every other, since they all hold the majors.

It also respects the lesson the style scorer learned the hard way: direction is
regime, not identity. The target's book is currently 52 positions and almost
entirely short, which is a market view, not a fingerprint. Direction only earns
anything here in combination with a rare market — being short a coin nobody else
touches is evidence; being short BTC is Tuesday.

## Why this is EVIDENCE and never a tiering vector

A copy-trader holds the same basket, in the same direction, at the same time.
That is the entire point of copy-trading, and it is indistinguishable here from
being the same person — this project exists precisely because its owner copies
this trader by hand, so we know such wallets exist.

The roster therefore records the score as evidence and does NOT count it among
the independent vectors that promote a wallet. Two vectors confirm, and
"portfolio overlap plus behavioural similarity" is exactly the pair a copycat
produces. It informs a human looking at a lead; it must not promote one by
itself.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import calibration
from src.utils import DATA_DIR, save_latest

PORTFOLIO_DIR = DATA_DIR / "portfolio_overlap"

# Above this share of wallets a market is common enough to be worth nothing.
# Matches calibration's own notion so the two cannot drift apart.
COMMON_FREQUENCY = getattr(calibration, "COMMON_FREQUENCY", 0.10)

# A basket this small is not a basket. One shared position between two wallets
# holding two things each is coincidence, and the rarity weight cannot rescue it.
MIN_POSITIONS = 3


def basket(state: dict) -> dict:
    """(coin -> direction) from a clearinghouse state. Empty when unreadable."""
    out = {}
    positions = (state or {}).get("assetPositions")
    for entry in positions if isinstance(positions, list) else []:
        pos = entry.get("position") if isinstance(entry, dict) else None
        if not isinstance(pos, dict):
            continue
        coin = pos.get("coin")
        try:
            szi = float(pos.get("szi", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not coin or szi == 0:
            continue
        out[str(coin)] = "long" if szi > 0 else "short"
    return out


def _rarity(market: str, freq: dict) -> float:
    """0.0 for a market everyone holds, rising toward 1.0 for a rare one."""
    f = calibration.market_frequency(market, freq)
    if f >= COMMON_FREQUENCY:
        return 0.0
    return round(1.0 - (f / COMMON_FREQUENCY), 4)


def overlap_score(target: dict, candidate: dict, freq: dict) -> dict:
    """Rarity-weighted agreement between two open books.

    A rarity-weighted Jaccard: shared rare markets over all rare markets either
    holds. Plain overlap would reward a wallet simply for holding many things,
    and every large book overlaps every other on the majors.

    Same market opposite direction counts toward the union but not the
    intersection — they are both interested in it, and they disagree.
    """
    if len(target) < MIN_POSITIONS or len(candidate) < MIN_POSITIONS:
        return {"score": 0.0, "reason": "book too small to be a basket",
                "shared": [], "shared_rare": []}

    union_weight = 0.0
    shared_weight = 0.0
    shared, shared_rare = [], []
    for market in set(target) | set(candidate):
        weight = _rarity(market, freq)
        if weight <= 0:
            continue                      # everyone holds it; it says nothing
        union_weight += weight
        if target.get(market) and target.get(market) == candidate.get(market):
            shared_weight += weight
            shared.append(market)
            shared_rare.append({"market": market, "rarity": weight,
                                "direction": target[market]})

    if union_weight <= 0:
        return {"score": 0.0,
                "reason": "no rare markets on either side — only common ones",
                "shared": [], "shared_rare": []}
    return {
        "score": round(shared_weight / union_weight, 4),
        "shared": sorted(shared),
        "shared_rare": sorted(shared_rare, key=lambda s: -s["rarity"])[:10],
        "rare_markets_considered": round(union_weight, 4),
    }


def build_report(target_state: dict, candidate_states: dict,
                 freq: dict | None = None) -> dict:
    freq = freq if freq is not None else calibration.load_market_frequencies()
    t = basket(target_state)
    scored = {}
    for wallet, state in (candidate_states or {}).items():
        result = overlap_score(t, basket(state), freq)
        if result.get("score", 0) > 0:
            scored[(wallet or "").lower()] = result
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "target_positions": len(t),
        "eligible_wallets": int((freq or {}).get("eligible_wallets", 0) or 0),
        "candidates_scored": len(candidate_states or {}),
        "overlaps": dict(sorted(scored.items(),
                                key=lambda kv: -kv[1]["score"])),
    }


def save(report: dict) -> None:
    save_latest(str(PORTFOLIO_DIR), report)
