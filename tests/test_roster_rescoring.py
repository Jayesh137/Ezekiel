# tests/test_roster_rescoring.py
"""Behaviour must be able to corroborate what other vectors found.

The scanner's priority sources never included a wallet found by dormancy,
linkage, identity or a deposit-address sentinel, and the leaderboard sweep takes
the largest accounts, so a fresh wallet those vectors flagged could never earn
the behavioural vote that promotes a lead into the close watch. And since a
score only votes when the validated scorer produced it, a stale high score must
be re-measured rather than silently ageing out.
"""

import json

from src import scanner
from src import thresholds as th

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TREASURY = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
CONFIG = {"target_wallet": T, "known_self_wallets": [TREASURY],
          "watch_wallets": ["0x" + "dd" * 20]}


def _row(addr, tier="POSSIBLE", service=False):
    return {"wallet": addr, "tier": tier, "is_service": service, "vectors": ["dormancy_handoff"]}


def test_roster_leads_are_queued_but_not_config_truth_or_services():
    roster = {"wallets": [_row(TREASURY, "CONFIRMED"), _row("0x" + "01" * 20),
                          _row("0x" + "02" * 20, "INFRASTRUCTURE", service=True),
                          _row(T, "CONFIRMED")]}
    got = scanner.roster_rescore_targets(CONFIG, roster, [])
    assert "0x" + "01" * 20 in got and got["0x" + "01" * 20]["source"] == "roster_lead"
    assert "0x" + "dd" * 20 in got, "the operator's watch list is pinned"
    assert TREASURY not in got and T not in got
    assert "0x" + "02" * 20 not in got


def test_only_stale_high_unvetoed_scores_are_rescored_highest_first():
    stale_hi = {"wallet": "0x" + "a1" * 20, "latest_score": 0.70, "latest_evidence": {"vetoes": []}}
    stale_lo = {"wallet": "0x" + "a2" * 20, "latest_score": 0.50, "latest_evidence": {"vetoes": []}}
    vetoed = {"wallet": "0x" + "a3" * 20, "latest_score": 0.90,
              "latest_evidence": {"vetoes": ["freq 11x"]}}
    current = {"wallet": "0x" + "a4" * 20, "latest_score": 0.80,
               "latest_scoring_schema": th.SCORING_SCHEMA, "latest_evidence": {"vetoes": []}}
    stale_mid = {"wallet": "0x" + "a5" * 20, "latest_score": 0.66, "latest_evidence": {}}
    got = scanner.roster_rescore_targets({"target_wallet": T}, {},
                                         [stale_lo, vetoed, current, stale_mid, stale_hi],
                                         stale_limit=1)
    assert list(got) == ["0x" + "a1" * 20]
    assert got["0x" + "a1" * 20]["source"] == "stale_score"


def test_neither_new_source_counts_as_corroboration():
    """A re-score cannot promote a wallet by itself; the roster decides."""
    assert "roster_lead" not in scanner.CORROBORATING_SOURCES
    assert "stale_score" not in scanner.CORROBORATING_SOURCES
    assert scanner._combined_route({}, "roster_lead") is None
    assert scanner._combined_route({}, "stale_score") is None


def test_priority_scan_includes_roster_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "DATA_DIR", tmp_path)
    lead = "0x" + "b7" * 20
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": [_row(lead)]}))
    scanned = []
    monkeypatch.setattr(scanner, "get_recent_bridge_depositors", lambda: [])
    monkeypatch.setattr(scanner, "scan_specific_wallet",
                        lambda wallet, *a, **k: scanned.append((wallet, k.get("source"))) or None)
    scanner.scan_priority_targets({}, {"target_wallet": T, "scanner": {}}, {"low": 0.65})
    assert (lead, "roster_lead") in scanned
