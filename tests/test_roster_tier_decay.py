# tests/test_roster_tier_decay.py
"""A wallet that LOSES evidence must not look like one that never had any.

The roster is rebuilt from scratch every run and tiers on the vectors currently
supported, which is right — a change inside one detector must not silently move
a tier. But it means a wallet whose evidence lapses is rewritten as though the
evidence never existed, and the file carries no trace of the demotion.

Measured live 2026-09-12. `0xdd53c529…` was PROBABLE on amount correlation and
dormancy. A refactor split the candidate pools and destroyed the bridge pool's
stored answer, so the correlation vector vanished; the wallet fell to WATCH; at
WATCH it sorted 166th of 180 and was cut by all four detector caps of 40, so
dormancy could no longer score it either. Two vectors became none, the wallet
the operator is watching by name became indistinguishable from 165 strangers,
and nothing anywhere said a tier had moved.

This is rule 5 over time: "we no longer have the evidence" is not "there was
never anything here". The demotion is recorded, and it is NOT alerted — a lost
inference is a fact about our own coverage, not a contact with his world, and
CLAUDE.md's alert discipline is the reason this project's CRITICALs still mean
something.
"""

from src import roster


def _doc(*rows):
    return {"wallets": [{"wallet": w, "tier": t} for w, t in rows]}


def test_a_wallet_that_never_moved_carries_no_demotion():
    rows = [{"wallet": "0xaa", "tier": "POSSIBLE"}]
    roster.carry_peak_tier(rows, _doc(("0xaa", "POSSIBLE")))
    assert rows[0]["peak_tier"] == "POSSIBLE"
    assert rows[0].get("tier_dropped_from") is None


def test_a_demoted_wallet_remembers_what_it_was():
    rows = [{"wallet": "0xaa", "tier": "WATCH"}]
    roster.carry_peak_tier(rows, _doc(("0xaa", "PROBABLE")))
    assert rows[0]["peak_tier"] == "PROBABLE"
    assert rows[0]["tier_dropped_from"] == "PROBABLE"


def test_a_promoted_wallet_raises_its_peak_and_reports_no_drop():
    rows = [{"wallet": "0xaa", "tier": "CONFIRMED"}]
    roster.carry_peak_tier(rows, _doc(("0xaa", "POSSIBLE")))
    assert rows[0]["peak_tier"] == "CONFIRMED"
    assert rows[0].get("tier_dropped_from") is None


def test_the_peak_survives_a_run_that_demoted_it():
    """The peak is the highest EVER reached, not the previous run's tier."""
    previous = {"wallets": [{"wallet": "0xaa", "tier": "WATCH", "peak_tier": "CONFIRMED"}]}
    rows = [{"wallet": "0xaa", "tier": "WATCH"}]
    roster.carry_peak_tier(rows, previous)
    assert rows[0]["peak_tier"] == "CONFIRMED"
    assert rows[0]["tier_dropped_from"] == "CONFIRMED"


def test_a_wallet_seen_for_the_first_time_peaks_where_it_starts():
    rows = [{"wallet": "0xnew", "tier": "POSSIBLE"}]
    roster.carry_peak_tier(rows, _doc(("0xother", "CONFIRMED")))
    assert rows[0]["peak_tier"] == "POSSIBLE"
    assert rows[0].get("tier_dropped_from") is None


def test_infrastructure_is_not_a_demotion():
    """Grading a wallet as infrastructure is a MEASUREMENT, not lost evidence.

    `0xd7a827fb…` was POSSIBLE until its 590,836 Arbitrum transactions were
    counted. Reporting that as a demotion would invert the finding."""
    rows = [{"wallet": "0xaa", "tier": "INFRASTRUCTURE"}]
    roster.carry_peak_tier(rows, _doc(("0xaa", "PROBABLE")))
    assert rows[0].get("tier_dropped_from") is None


def test_a_missing_or_unreadable_previous_roster_is_not_a_demotion():
    rows = [{"wallet": "0xaa", "tier": "WATCH"}]
    roster.carry_peak_tier(rows, None)
    assert rows[0]["peak_tier"] == "WATCH"
    assert rows[0].get("tier_dropped_from") is None


def test_build_roster_counts_the_demotions(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps(
        {"wallets": [{"wallet": "0xaa", "tier": "PROBABLE", "peak_tier": "PROBABLE"}]}))
    (tmp_path / "transfer_graph").mkdir(parents=True)
    (tmp_path / "transfer_graph" / "latest.json").write_text(json.dumps(
        {"nodes": [{"wallet": "0xaa", "confidence": 0.0, "evidence": {}}]}))

    doc = roster.build_roster({"target_wallet": "0xtarget", "known_self_wallets": []})

    ((row,)) = [w for w in doc["wallets"] if w["wallet"] == "0xaa"]
    assert row["tier"] == "WATCH" and row["tier_dropped_from"] == "PROBABLE"
    assert doc["demoted_count"] == 1
