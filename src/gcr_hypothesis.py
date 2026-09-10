# src/gcr_hypothesis.py
"""Testing whether the tracked wallet behaves like GCR — in both directions.

The operator puts the odds at 55-75%. That is a strong hypothesis, not a
premise, and the only honest way to use GCR's documented style is as something
that can move the estimate DOWN as well as up. A test that can only confirm is
worth nothing.

Two rules this module exists to enforce:

  1. **No circular reasoning.** Never conclude "the target is GCR, GCR does X,
     therefore a wallet doing X is the target". The GCR profile is an
     INDEPENDENT reference. Its value is precisely that a wallet can match it
     with no connection to the target at all — which is the case that matters if
     he moves to a wallet we cannot reach by flow.

  2. **Absence of evidence is not evidence.** A trait we cannot measure returns
     UNTESTABLE, never "consistent". Counting unmeasured traits as agreement is
     how a profile quietly confirms itself.

The source material is public writing from Feb 2021 - Apr 2023. The target's
on-chain history starts 2026-02-05. Specific trades cannot bridge that gap; only
enduring style can, and people change — so a contradiction is evidence, not
proof.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import calibration
from src.utils import DATA_DIR, save_latest

HYPOTHESIS_DIR = DATA_DIR / "gcr_hypothesis"
REFERENCE_PATH = Path(__file__).parent.parent / "research" / "gcr_reference.json"

CONSISTENT = "consistent"
CONTRADICTS = "contradicts"
UNTESTABLE = "untestable"

# Below this share of wallets a market stands in for "small cap". A proxy, and
# an imperfect one: kSHIB is a large token that few Hyperliquid accounts trade.
# Findings that lean on it must say so.
SMALL_CAP_FREQUENCY = 0.02


def load_reference(path: Path | None = None) -> dict:
    try:
        with open(path or REFERENCE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def positions_from(state: dict) -> list[dict]:
    out = []
    for entry in (state or {}).get("assetPositions") or []:
        pos = entry.get("position") if isinstance(entry, dict) else None
        if not isinstance(pos, dict):
            continue
        try:
            szi = float(pos.get("szi", 0) or 0)
            ntl = abs(float(pos.get("positionValue", 0) or 0))
        except (TypeError, ValueError):
            continue
        if not pos.get("coin") or szi == 0:
            continue
        out.append({"coin": pos["coin"], "direction": "long" if szi > 0 else "short",
                    "notional": ntl})
    return out


def check_net_short_bias(positions: list[dict], **_) -> dict:
    """'Bear at heart' — is the book net short?"""
    if not positions:
        return {"verdict": UNTESTABLE, "detail": "no open positions to judge"}
    shorts = sum(1 for p in positions if p["direction"] == "short")
    share = shorts / len(positions)
    return {
        "verdict": CONSISTENT if share >= 0.7 else
                   CONTRADICTS if share <= 0.3 else UNTESTABLE,
        "detail": f"{shorts} of {len(positions)} positions short ({share:.0%})",
    }


def check_hedged_book(positions: list[dict], **_) -> dict:
    """'Short weaker alts against your favourite plays' — a two-sided book.

    A book with no longs at all is directional, not hedged, and that is a real
    difference from the structure he described.
    """
    if not positions:
        return {"verdict": UNTESTABLE, "detail": "no open positions to judge"}
    longs = sum(1 for p in positions if p["direction"] == "long")
    shorts = len(positions) - longs
    if longs and shorts:
        return {"verdict": CONSISTENT,
                "detail": f"two-sided: {longs} long, {shorts} short"}
    return {"verdict": CONTRADICTS,
            "detail": f"one-sided: {longs} long, {shorts} short — "
                      f"directional rather than the hedged structure described"}


def check_shorts_small_caps(positions: list[dict], freq: dict | None = None,
                           **_) -> dict:
    """'Never short micro to small caps' — but read the caveat.

    His own posts describe fading listing pumps on IOTX, TRU, CLV, MNGO and AXS,
    all small caps, so the rule means "do not short CORNERED, exhausted small
    caps" rather than "never short anything small". A small-cap short therefore
    returns UNTESTABLE rather than CONTRADICTS: we cannot tell a listing-pump
    fade from the thing he warned against, and calling it a contradiction would
    be reading the distilled slogan over his actual behaviour.
    """
    if not positions or not freq:
        return {"verdict": UNTESTABLE, "detail": "no positions or no market frequencies"}
    shorts = [p for p in positions if p["direction"] == "short"]
    if not shorts:
        # Vacuous: holding no shorts at all cannot demonstrate restraint about
        # WHICH things one shorts. Counting it as agreement is how a profile
        # confirms itself on absent evidence.
        return {"verdict": UNTESTABLE,
                "detail": "holds no shorts, so the rule cannot be exercised"}
    small = [p for p in shorts
             if calibration.market_frequency(p["coin"], freq) < SMALL_CAP_FREQUENCY]
    if not small:
        return {"verdict": CONSISTENT, "detail": "shorts no thinly-held markets"}
    names = ", ".join(p["coin"] for p in sorted(small, key=lambda p: -p["notional"])[:5])
    return {
        "verdict": UNTESTABLE,
        "detail": f"shorts {len(small)} thinly-held market(s) ({names}). His own "
                  f"posts fade small-cap listing pumps, so this neither confirms "
                  f"nor contradicts — and Hyperliquid rarity is only a proxy for "
                  f"market cap.",
    }


def check_round_number_affinity(_positions=None, amounts: list | None = None,
                               **__) -> dict:
    """'Round numbers are Schelling points' — does he move round amounts?"""
    if not amounts:
        return {"verdict": UNTESTABLE, "detail": "no transfer amounts available"}
    round_ = sum(1 for a in amounts if a >= 1000 and abs(a - round(a, -5)) < 1)
    share = round_ / len(amounts)
    return {
        "verdict": CONSISTENT if share >= 0.3 else UNTESTABLE,
        "detail": f"{round_} of {len(amounts)} transfers land on a round 100k "
                  f"({share:.0%}). Weak on its own — most large traders send "
                  f"round numbers.",
    }


# Named check_* rather than test_*: pytest collects anything called test_* from
# an imported module, and production helpers are not tests.
CHECKS = {
    "net_short_bias": check_net_short_bias,
    "hedged_book": check_hedged_book,
    "shorts_small_caps": check_shorts_small_caps,
    "round_number_affinity": check_round_number_affinity,
}


def evaluate(state: dict, freq: dict | None = None,
             amounts: list | None = None) -> dict:
    positions = positions_from(state)
    results = {}
    for name, fn in CHECKS.items():
        results[name] = fn(positions, freq=freq, amounts=amounts)

    tallies = {CONSISTENT: 0, CONTRADICTS: 0, UNTESTABLE: 0}
    for r in results.values():
        tallies[r["verdict"]] += 1

    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "open_positions": len(positions),
        "results": results,
        "tally": tallies,
        # Deliberately no single probability. Four style traits cannot be
        # combined into a number that means anything, and printing one would
        # invite exactly the false precision this module exists to avoid.
        "reading": _reading(tallies),
        "prior_note": "Operator's prior is 55-75%. These are style traits from "
                      "2021-2023 writing against on-chain behaviour from 2026 — "
                      "they should nudge that estimate, never replace it.",
    }


def _reading(tallies: dict) -> str:
    if tallies[CONTRADICTS] and not tallies[CONSISTENT]:
        return ("Every testable trait points AWAY. Worth taking seriously as "
                "evidence against the hypothesis.")
    if tallies[CONSISTENT] and not tallies[CONTRADICTS]:
        return ("Testable traits are consistent. Weak support — style traits are "
                "shared by many traders.")
    if tallies[CONSISTENT] and tallies[CONTRADICTS]:
        return ("Mixed: some traits fit and some do not. Read the individual "
                "verdicts rather than the count.")
    return "Nothing testable resolved. No movement in either direction."


def save(report: dict) -> None:
    save_latest(str(HYPOTHESIS_DIR), report)
