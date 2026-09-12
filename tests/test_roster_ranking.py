# tests/test_roster_ranking.py
"""What decides which wallets a bounded budget actually looks at.

`confidence` is produced by the transfer graph alone. A wallet reached by any
OTHER vector — an amount correlation, a dormancy handoff, an explicit Hyperliquid
link — therefore carries confidence 0.0 by construction, which is rule 6 inside
the ranking: a missing reading priced as zero, invisible to every comparison.

Measured live 2026-09-12: 124 of 181 non-infrastructure wallets (69%) carried
exactly 0.0, and 75 of them tied inside POSSIBLE, so the sort fell through to its
last key — the wallet ADDRESS. Which leads the per-wallet detectors looked at was
being decided by leading hex digits. The correlation lead at 0.9974 confidence
sat at #102 against a cap of 119 because its address begins `0x7f`; the one at
0.6839 sat at #130 and was cut, while holding 33 live named agents in a
`chip_oe*` family that nothing had ever indexed.

That is the `expanded_ledger` rule again: when a cap trims a collection, ask what
the sort order MEANS. For a chase queue it is priority, and an address is not one.
"""

from src import roster


def _row(wallet, tier="POSSIBLE", confidence=0.0, vectors=(), **evidence):
    return {"wallet": wallet, "tier": tier, "confidence": confidence,
            "vectors": list(vectors), "vector_count": len(vectors),
            "evidence": evidence}


def test_a_transfer_graph_confidence_is_its_strength():
    assert roster.evidence_strength(_row("0xaa", confidence=0.58)) == 0.58


def test_a_correlation_only_wallet_is_ranked_on_its_correlation():
    row = _row("0xaa", vectors=["correlation"], correlation_confidence=0.9974)
    assert roster.evidence_strength(row) == 0.9974


def test_a_dormancy_only_wallet_is_ranked_on_its_handoff_score():
    row = _row("0xaa", vectors=["dormancy_handoff"],
               dormancy_handoff={"score": 0.4286})
    assert roster.evidence_strength(row) == 0.4286


def test_the_strongest_reading_wins_when_several_vectors_agree():
    row = _row("0xaa", confidence=0.3, vectors=["transfer", "correlation"],
               correlation_confidence=0.88)
    assert roster.evidence_strength(row) == 0.88


def test_a_wallet_with_no_score_anywhere_is_zero_not_an_error():
    assert roster.evidence_strength(_row("0xaa")) == 0.0


def test_an_unparseable_score_never_raises():
    row = _row("0xaa", correlation_confidence="not a number")
    assert roster.evidence_strength(row) == 0.0


def test_the_correlation_lead_outranks_a_transfer_wallet_it_used_to_sort_below():
    """The live regression: 0.9974 correlation vs 0.16 transfer confidence."""
    lead = _row("0xff", vectors=["correlation"], correlation_confidence=0.9974)
    weak = _row("0x00", confidence=0.16, vectors=["transfer"])
    ranked = sorted([weak, lead], key=roster.rank_key)
    assert [r["wallet"] for r in ranked] == ["0xff", "0x00"]


def test_more_agreeing_vectors_still_outrank_one_strong_one():
    """Two independent vectors is the project's whole premise; it stays primary."""
    two = _row("0xff", confidence=0.2, vectors=["transfer", "linkage"])
    one = _row("0x00", vectors=["correlation"], correlation_confidence=0.99)
    ranked = sorted([one, two], key=roster.rank_key)
    assert [r["wallet"] for r in ranked] == ["0xff", "0x00"]


def test_the_address_is_only_the_last_resort_tiebreaker():
    a = _row("0xbb", confidence=0.5, vectors=["transfer"])
    b = _row("0xaa", confidence=0.5, vectors=["transfer"])
    assert [r["wallet"] for r in sorted([a, b], key=roster.rank_key)] == ["0xaa", "0xbb"]


def test_build_roster_orders_by_strength_not_by_address(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path / "transfer_graph").mkdir(parents=True)
    (tmp_path / "transfer_graph" / "latest.json").write_text(json.dumps({"nodes": []}))
    (tmp_path / "correlations").mkdir(parents=True)
    (tmp_path / "correlations" / "latest.json").write_text(json.dumps({"matches": [
        {"wallet": "0xff", "confidence": 0.9974},
        {"wallet": "0x00", "confidence": 0.10},
    ]}))

    doc = roster.build_roster({"target_wallet": "0xtarget", "known_self_wallets": []})

    assert [w["wallet"] for w in doc["wallets"]] == ["0xff", "0x00"]


def test_each_row_carries_the_strength_that_ordered_it():
    """Serialised so a reader can see WHY a wallet sits where it does.

    Without it the dashboard shows a correlation lead at rank 2 with a
    Confidence column reading 0%, which is rule 6 in the UI: `confidence` comes
    from the transfer graph alone, so a wallet it never scored has no reading
    there — and no reading must render as "—", never as a measured zero.
    """
    import json

    rows = [{"wallet": "0xaa", "tier": "POSSIBLE", "confidence": 0.0,
             "vectors": ["correlation"], "vector_count": 1,
             "evidence": {"correlation_confidence": 0.9974}}]
    roster.attach_rank_strength(rows)
    assert rows[0]["rank_strength"] == 0.9974
    # and it must survive a JSON round trip as a plain number
    assert json.loads(json.dumps(rows))[0]["rank_strength"] == 0.9974


def test_build_roster_serialises_rank_strength(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    monkeypatch.setattr(roster, "ROSTER_DIR", tmp_path / "roster")
    (tmp_path / "transfer_graph").mkdir(parents=True)
    (tmp_path / "transfer_graph" / "latest.json").write_text(json.dumps({"nodes": []}))
    (tmp_path / "correlations").mkdir(parents=True)
    (tmp_path / "correlations" / "latest.json").write_text(json.dumps(
        {"matches": [{"wallet": "0xff", "confidence": 0.9974}]}))

    doc = roster.build_roster({"target_wallet": "0xt", "known_self_wallets": []})
    assert doc["wallets"][0]["rank_strength"] == 0.9974
