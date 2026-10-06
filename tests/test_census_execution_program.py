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


def test_population_is_a_deterministic_uniform_sample_not_volume_ranked():
    # A uniform sample of the eligible band is the right negative population for a
    # false-positive threshold, and reaches clip-style traders far sooner than a
    # volume-desc walk that front-loads market makers. Order is stable (same input
    # -> same order) and independent of volume rank.
    lb = [_row(f"0x{i:040x}", 1_000_000, 1_000_000 + i) for i in range(50)]
    first = census.population(lb, exclude=set())
    assert sorted(first) == sorted(a["ethAddress"] for a in lb)  # covers everyone
    assert census.population(lb, exclude=set()) == first          # deterministic
    # Not the volume order (which would be strictly ascending-by-i reversed).
    assert first != [a["ethAddress"] for a in sorted(lb, key=lambda r: -int(r["windowPerformances"][0][1]["vlm"]))]


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


def test_census_summary_reports_a_rho_distribution():
    census = census_summary_fixture()
    assert census["rho_p99"] is not None
    assert census["rho_population"] == 3


def census_summary_fixture():
    from src import execution_program as ep
    return ep.summarise_census([0.0, 0.2, 1.0], rhos=[0.1, 0.5, 0.95])


def test_state_lives_in_the_committed_tree():
    # bb21cb3092 moved the census state out of gitignored data/.local so the
    # population could build across ephemeral Actions runs, but the old
    # assignment stayed beneath the new one and won: every run started from an
    # empty state, and the census sat at 1 measured account of 83.
    assert census.STATE == census.OUT_DIR / "census_state.json"
    assert ".local" not in census.STATE.parts
