# tests/test_market_alert_routing.py
"""HIP-3 / xyz alert routing must obey MEASURED rarity, not a name prefix.

A live sweep logged "xyz: SIGNATURE MATCH ... ['xyz:BRENTOIL']" for many wallets
and attempted CRITICAL "Same Rare HIP-3 Markets" alerts, even though BRENTOIL had
already been measured at ~26% of eligible wallets with rarity bonus 0.0.

Cause: get_asset_overlap built rare_overlap as
    [c for c in overlap if c.startswith("xyz:")]
so the alert route classified rarity by market NAME and never consulted the
persisted calibration. The scoring bonus was gated on measurement; the alert was
not.

These tests pin the corrected contract.
"""

import pytest

from src import calibration as c
from src import scanner
from src import thresholds as th

RARE = "xyz:UNOBTAINIUM"
COMMON = "xyz:BRENTOIL"
UNUSUAL = "xyz:MIDCAP"


def freq(eligible: int, counts: dict) -> dict:
    return c.summarise_market_observations([{
        "recorded_at": "2026-07-27T00:00:00+00:00",
        "eligible_wallets": eligible, "market_counts": counts,
    }])


MEASURED = freq(200, {COMMON: 52, UNUSUAL: 6, RARE: 1})
THIN = freq(10, {RARE: 0})
EFF = {"high": 0.60, "medium": 0.55, "low": 0.50, "policy": th.SRC_CURRENT_VALIDATED}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No production cooldowns, no persisted calibration, no real sends."""
    sent = []
    monkeypatch.setattr("src.alerts.alert_xyz_signature_match",
                        lambda *a, **k: sent.append(a[0]) or True)
    monkeypatch.setattr(scanner, "_sustained_high", lambda *a, **k: True)
    scanner._market_alert_sets_this_run.clear()
    return sent


def result(markets, score=0.72, vetoes=None, rarity=MEASURED, source=None):
    """A scan result shaped exactly as compute_similarity emits it."""
    return {
        "wallet": "0x" + "a" * 40,
        "score": score,
        "source": source,
        "evidence": {
            "vetoes": vetoes or [],
            "asset_overlap": {"rare_overlap": list(markets)},
            "market_rarity": {
                "shared_markets": list(markets),
                "rare_markets": c.rare_markets(markets, rarity),
                "classification": {m: c.classify_market(m, rarity) for m in markets},
                "description": c.describe_markets(markets, rarity),
                "sample_sufficient": bool(rarity.get("sufficient")),
                "bonus_applied": c.market_rarity_bonus(markets, rarity)[0],
                "score_without_bonus": score - c.market_rarity_bonus(markets, rarity)[0],
            },
        },
    }


# --- classification is the single authority ---------------------------------------

def test_common_market_is_never_classified_rare():
    assert c.classify_market(COMMON, MEASURED) == c.MARKET_COMMON
    assert c.rare_markets([COMMON], MEASURED) == []
    assert scanner._measured_rare_markets(result([COMMON])) == []


def test_insufficient_sample_is_unknown_never_rare():
    assert c.classify_market(RARE, THIN) == c.MARKET_UNKNOWN
    assert c.rare_markets([RARE], THIN) == []
    assert scanner._measured_rare_markets(result([RARE], rarity=THIN)) == []


def test_genuinely_rare_market_is_classified_rare():
    assert c.classify_market(RARE, MEASURED) == c.MARKET_RARE
    assert c.rare_markets([RARE, COMMON, UNUSUAL], MEASURED) == [RARE]


def test_unusual_market_earns_a_bonus_but_is_not_callable_rare():
    """A small bonus and "rare enough to headline a CRITICAL alert" differ."""
    assert c.classify_market(UNUSUAL, MEASURED) == c.MARKET_UNUSUAL
    assert c.market_rarity_bonus([UNUSUAL], MEASURED)[0] > 0
    assert c.rare_markets([UNUSUAL], MEASURED) == []


def test_description_states_the_measured_class_accurately():
    d = c.describe_markets([COMMON, RARE], MEASURED)
    assert "common" in d and "rare" in d
    assert "%" in d
    assert "UNMEASURED" in c.describe_markets([RARE], THIN)


# --- alert routing ------------------------------------------------------------------

def test_common_market_produces_zero_critical_alerts(_isolate):
    r = result([COMMON])
    assert scanner._measured_rare_markets(r) == []
    d = th.disposition(r["score"], EFF, rare_overlap=True,
                       score_without_market_bonus=r["evidence"]["market_rarity"]["score_without_bonus"])
    # Evidence is retained, but nothing in the market path can promote it.
    assert d["action"] in (th.ACTION_ALERT, th.ACTION_WATCHLIST)
    assert _isolate == [], "a common market must never send an alert"


def test_rare_market_with_insufficient_sample_sends_nothing(_isolate):
    assert scanner._measured_rare_markets(result([RARE], rarity=THIN)) == []
    assert _isolate == []


def test_rare_market_alone_is_at_most_watchlist(_isolate):
    """No independent corroboration -> the market cannot promote by itself."""
    r = result([RARE], score=0.72)
    assert scanner._measured_rare_markets(r) == [RARE]
    assert scanner._is_corroborated(r) is False
    d = th.disposition(0.72, EFF, rare_overlap=True,
                       score_without_market_bonus=0.55, corroborated=False)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("shared-market bonus" in b for b in d["blockers"])
    assert _isolate == []


def test_rare_market_plus_independent_evidence_is_alert_eligible():
    r = result([RARE], score=0.72, source="fund_flow")
    assert scanner._is_corroborated(r, "fund_flow") is True
    d = th.disposition(0.72, EFF, rare_overlap=True,
                       score_without_market_bonus=0.65, corroborated=True)
    assert d["action"] == th.ACTION_ALERT


def test_vetoed_market_match_never_alerts(_isolate):
    r = result([RARE], vetoes=["Decision frequency 20x apart"], source="fund_flow")
    assert th.can_alert(r["evidence"]["vetoes"]) is False
    d = th.disposition(0.99, EFF, vetoes=r["evidence"]["vetoes"],
                       rare_overlap=True, corroborated=True)
    assert d["action"] != th.ACTION_ALERT
    assert _isolate == []


def test_market_bonus_alone_cannot_clear_the_high_threshold():
    """score_without_market_bonus protection applies on the market route too."""
    d = th.disposition(EFF["high"] + 0.02, EFF, rare_overlap=True,
                       score_without_market_bonus=EFF["high"] - 0.03,
                       corroborated=True)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("only via the shared-market bonus" in b for b in d["blockers"])


# --- deduplication -------------------------------------------------------------------

def test_repeated_same_market_matches_are_deduplicated():
    """Six wallets sharing one market is one finding repeated, not six."""
    scanner._market_alert_sets_this_run.clear()
    assert scanner._market_alert_allowed([RARE]) is True
    for _ in range(5):
        assert scanner._market_alert_allowed([RARE]) is False, "duplicate not suppressed"


def test_distinct_market_sets_are_not_deduplicated_together():
    scanner._market_alert_sets_this_run.clear()
    assert scanner._market_alert_allowed([RARE]) is True
    assert scanner._market_alert_allowed([RARE, UNUSUAL]) is True
    assert scanner._market_alert_allowed([RARE]) is False


def test_per_run_alert_cap_bounds_the_blast_radius():
    scanner._market_alert_sets_this_run.clear()
    for i in range(scanner.MAX_MARKET_ALERTS_PER_RUN):
        assert scanner._market_alert_allowed([f"xyz:M{i}"]) is True
    assert scanner._market_alert_allowed(["xyz:OVERFLOW"]) is False


def test_dedup_state_resets_between_runs():
    scanner._market_alert_sets_this_run.clear()
    assert scanner._market_alert_allowed([RARE]) is True
    scanner._market_alert_sets_this_run.clear()          # next sweep
    assert scanner._market_alert_allowed([RARE]) is True


# --- migration correlation -----------------------------------------------------------

def test_migration_correlation_waives_persistence_but_not_the_rest():
    """Silence is independent corroboration, so it may waive persistence — but
    vetoes, the percentile gate and the market-bonus guard still apply."""
    waived = th.disposition(0.6967, EFF, sustained=False, corroborated=True)
    assert waived["action"] == th.ACTION_ALERT, "silence should waive persistence"

    vetoed = th.disposition(0.6967, EFF, sustained=False, corroborated=True,
                            vetoes=["style veto"])
    assert vetoed["action"] != th.ACTION_ALERT

    gated = th.disposition(0.6967, EFF, sustained=False, corroborated=True,
                           percentile_ok=False)
    assert gated["action"] != th.ACTION_ALERT

    market_only = th.disposition(0.6967, EFF, sustained=False, corroborated=True,
                                 rare_overlap=True,
                                 score_without_market_bonus=0.55)
    assert market_only["action"] != th.ACTION_ALERT


def test_migration_correlation_without_corroboration_still_needs_persistence():
    d = th.disposition(0.6967, EFF, sustained=False, corroborated=False)
    assert d["action"] == th.ACTION_WATCHLIST
    assert any("persistence" in b for b in d["blockers"])


# --- subaccounts null handling --------------------------------------------------------

@pytest.mark.parametrize("payload,expected", [
    (None, []),                                   # HL returns null when none exist
    ([], []),
    ({"subaccounts": None}, []),
    ({"subaccounts": [{"user": "0xA"}]}, [{"user": "0xA"}]),
    ([{"user": "0xB"}], [{"user": "0xB"}]),
])
def test_subaccounts_payload_shapes(payload, expected):
    if isinstance(payload, list):
        out = payload
    elif isinstance(payload, dict):
        out = payload.get("subaccounts") or []
    else:
        out = []
    assert out == expected
