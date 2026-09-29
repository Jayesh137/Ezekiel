"""Census population selection and the resumable measurement loop."""

from scripts import census_execution_program as census


def _row(addr, value, week_vlm):
    return {"ethAddress": addr, "accountValue": str(value),
            "windowPerformances": [["week", {"vlm": str(week_vlm)}]]}


def test_population_drops_small_idle_and_extreme_volume_accounts():
    lb = [_row("0xbig", 1_000_000, 5_000_000), _row("0xsmall", 100, 5_000_000),
          _row("0xidle", 1_000_000, 0), _row("0xMM", 1_000_000, 9_000_000_000)]
    assert census.population(lb, exclude=set()) == ["0xbig"]


def test_population_excludes_the_configured_cluster():
    lb = [_row("0xTarget", 1_000_000, 5_000_000), _row("0xother", 1_000_000, 4_000_000)]
    assert census.population(lb, exclude={"0xtarget"}) == ["0xother"]


def test_population_orders_by_week_volume_descending():
    lb = [_row("0xa", 1_000_000, 1_000_000), _row("0xb", 1_000_000, 9_000_000)]
    assert census.population(lb, exclude=set()) == ["0xb", "0xa"]


def test_register_hits_makes_each_hit_an_investigation_candidate(tmp_path):
    from src.candidate_registry import iter_candidates
    hits = {
        "0x1111111111111111111111111111111111111111": {
            "wallet": "0x1111111111111111111111111111111111111111",
            "clips_matched": 5, "clip_match_ratio": 0.83},
    }
    census.register_hits(hits, data_dir=tmp_path)
    rows = iter_candidates(tmp_path)
    assert [r["wallet"] for r in rows] == ["0x1111111111111111111111111111111111111111"]
    assert "execution_program_census" in rows[0]["discovery_sources"]


def test_register_hits_ignores_malformed_addresses(tmp_path):
    from src.candidate_registry import iter_candidates
    census.register_hits({"not-an-address": {"clips_matched": 5}}, data_dir=tmp_path)
    assert iter_candidates(tmp_path) == []


def test_cap_state_trims_oldest_processed_but_keeps_all_hits():
    processed = {f"0x{i:040x}": {"ratio": None, "at": i} for i in range(census.MAX_STATE_ROWS + 50)}
    hits = {"0xhit": {"clips_matched": 6}}
    capped = census.cap_state({"processed": processed, "hits": hits})
    assert len(capped["processed"]) == census.MAX_STATE_ROWS
    assert capped["hits"] == hits
    # The oldest (smallest 'at') were dropped; the newest kept.
    kept = capped["processed"]
    assert f"0x{census.MAX_STATE_ROWS + 49:040x}" in kept
    assert "0x" + "0" * 40 not in kept
