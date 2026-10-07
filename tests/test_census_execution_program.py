"""Census population selection and the resumable measurement loop."""

import pytest

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


def test_a_habit_row_needs_its_order_read():
    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": False, "error": "HTTP 500"}
        return {"ok": True, "data": []}
    assert census.measure_habits(fetch, "0x" + "1" * 40, []) is None


def test_a_habit_row_records_style_and_subaccounts():
    master, sub = "0x" + "1" * 40, "0x" + "2" * 40

    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": True, "data": []}
        return {"ok": True, "data": [{"subAccountUser": sub, "master": master}]}
    row = census.measure_habits(fetch, master, [])
    assert row["subaccounts"] == [sub] and row["style"] is None and row["orders_seen"] == 0


def test_a_failed_subaccount_read_is_unknown_not_none():
    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": True, "data": []}
        return {"ok": False, "error": "HTTP 500"}
    assert census.measure_habits(fetch, "0x" + "1" * 40, [])["subaccounts"] is None


def test_habit_rows_are_capped_newest_first():
    habits = {f"0x{i:040x}": {"at": i} for i in range(census.MAX_HABIT_ROWS + 5)}
    capped = census.cap_state({"processed": {}, "hits": {}, "habits": habits})
    assert len(capped["habits"]) == census.MAX_HABIT_ROWS
    assert f"0x{0:040x}" not in capped["habits"]


def test_run_keeps_a_habit_row_per_measured_account_and_counts_them(tmp_path, monkeypatch):
    # The wiring the unit tests above do not reach: run() stores each measured
    # account's habit row, an account whose order read failed is processed but has
    # no row (a failed read is never an empty row), and census.json says how many.
    import json

    out = tmp_path / "execution_program"
    monkeypatch.setattr(census, "OUT_DIR", out)
    monkeypatch.setattr(census, "STATE", out / "census_state.json")
    monkeypatch.setattr(census, "CENSUS_FILE", out / "census.json")
    monkeypatch.setattr(census, "target_signature",
                        lambda: {"clip_table": {"BTC": {"size": 0.1}}})
    good, unread, sub = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40

    def fetch(body):
        if body["type"] == "historicalOrders" and body["user"] == unread:
            return {"ok": False, "error": "HTTP 500"}
        if body["type"] == "subAccounts":
            return {"ok": True, "data": [{"subAccountUser": sub, "master": body["user"]}]}
        return {"ok": True, "data": []}

    leaderboard = [_row(good, 1_000_000, 5_000_000), _row(unread, 1_000_000, 5_000_000)]
    census.run(limit=10, budget_seconds=60, fetch=fetch, leaderboard=leaderboard)

    state = json.loads(census.STATE.read_text())
    assert set(state["processed"]) == {good, unread}
    assert list(state["habits"]) == [good]
    assert state["habits"][good]["subaccounts"] == [sub]
    assert json.loads(census.CENSUS_FILE.read_text())["habit_measured"] == 1


def test_load_state_reads_habits_back_and_upgrades_an_older_file(tmp_path, monkeypatch):
    # The committed state is the census's only memory between Actions runs: a load
    # that dropped `habits` would restart the habit census from empty every day.
    import json

    path = tmp_path / "census_state.json"
    monkeypatch.setattr(census, "STATE", path)
    habit = {"orders_seen": 500, "at": 1}
    path.write_text(json.dumps({"processed": {"0xa": {"at": 1}}, "hits": {},
                                "habits": {"0xa": habit}}))
    assert census.load_state()["habits"] == {"0xa": habit}
    # The file as committed before the habit census: no `habits` key at all.
    path.write_text(json.dumps({"processed": {"0xa": {"at": 1}}, "hits": {"0xh": {}}}))
    assert census.load_state() == {"processed": {"0xa": {"at": 1}}, "hits": {"0xh": {}},
                                   "habits": {}}
    path.unlink()
    assert census.load_state() == {"processed": {}, "hits": {}, "habits": {}}


def test_an_orders_answer_counts_only_if_it_is_ok_and_a_list():
    # A 200 whose body is an error object is as unread as an HTTP failure, and a
    # failed flag outranks whatever payload arrived with it (rule 5).
    def reading(answer):
        def fetch(body):
            return answer if body["type"] == "historicalOrders" else {"ok": True, "data": []}
        return fetch
    me = "0x" + "1" * 40
    assert census.measure_habits(reading({"ok": True, "data": {"error": "x"}}), me, []) is None
    assert census.measure_habits(reading({"ok": False, "data": []}), me, []) is None
    assert census.measure_habits(reading({"ok": True, "data": []}), me, [])["orders_seen"] == 0


def test_a_habit_row_has_the_fields_the_panels_and_the_cap_read():
    master, a, b = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40

    def fetch(body):
        if body["type"] == "historicalOrders":
            return {"ok": True, "data": []}
        # Out of order and with a repeat: the stored list is sorted and unique, so
        # the committed file does not churn when the API reorders it.
        return {"ok": True, "data": [{"subAccountUser": b, "master": master},
                                     {"subAccountUser": a, "master": master},
                                     {"subAccountUser": b, "master": master}]}
    row = census.measure_habits(fetch, master, [])
    assert sorted(row) == sorted(["orders_seen", "shares", "ioc5", "style", "cadence",
                                  "clip_table", "clip_notionals", "program_runs",
                                  "subaccounts", "at"])
    assert row["subaccounts"] == [a, b]
    # `at` is what cap_state evicts by; a row without it would always be oldest.
    assert isinstance(row["at"], int) and row["at"] > 0


# --- the committed state is the census's only memory: a damaged one fails loudly ----------
#
# load_state used to read any OSError or ValueError as "no state yet", and run() then
# measured from empty and rewrote the whole file. One corrupt commit of
# census_state.json would have silently restarted weeks of accumulated population, the
# store that gates the execution_program vote and feeds the stranger panel.


def test_a_missing_state_file_is_a_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(census, "STATE", tmp_path / "census_state.json")
    assert census.load_state() == {"processed": {}, "hits": {}, "habits": {}}


def test_a_truncated_state_file_raises_and_names_the_file(tmp_path, monkeypatch):
    path = tmp_path / "census_state.json"
    monkeypatch.setattr(census, "STATE", path)
    path.write_text('{"processed": {"0xa": {"ratio": 0.5, "at": 1}, "0xb": {"ra')
    with pytest.raises(ValueError, match="census_state.json"):
        census.load_state()


@pytest.mark.parametrize("text", ["[]", '[{"processed": {}}]', "null", '"x"', "3", ""],
                         ids=["empty-list", "list", "null", "string", "number", "empty-file"])
def test_a_state_file_that_is_not_an_object_raises(tmp_path, monkeypatch, text):
    path = tmp_path / "census_state.json"
    monkeypatch.setattr(census, "STATE", path)
    path.write_text(text)
    with pytest.raises(ValueError, match="census_state.json"):
        census.load_state()


@pytest.mark.parametrize("state", [{"processed": []}, {"hits": []}, {"habits": "x"},
                                   {"processed": None}],
                         ids=["processed-list", "hits-list", "habits-string", "processed-null"])
def test_a_state_whose_parts_are_not_objects_raises(tmp_path, monkeypatch, state):
    import json

    path = tmp_path / "census_state.json"
    monkeypatch.setattr(census, "STATE", path)
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="census_state.json"):
        census.load_state()


def test_a_state_file_that_cannot_be_opened_raises(tmp_path, monkeypatch):
    # Anything other than "no such file" is a fault, not a first run: here the path is a directory.
    monkeypatch.setattr(census, "STATE", tmp_path)
    with pytest.raises(OSError):
        census.load_state()


def test_run_stops_on_a_corrupt_state_and_overwrites_nothing(tmp_path, monkeypatch):
    # The consequence that mattered: not the raise itself but the full rewrite that
    # followed it. The damaged file must survive byte for byte, and no census appears.
    out = tmp_path / "execution_program"
    out.mkdir()
    monkeypatch.setattr(census, "OUT_DIR", out)
    monkeypatch.setattr(census, "STATE", out / "census_state.json")
    monkeypatch.setattr(census, "CENSUS_FILE", out / "census.json")
    monkeypatch.setattr(census, "target_signature", lambda: {"clip_table": {"BTC": {"size": 0.1}}})
    damaged = '{"processed": {"0x' + "a" * 40 + '": {"ratio": 0.5, "at": 1}, "0xb'
    census.STATE.write_text(damaged)
    fetched = []

    def fetch(body):
        fetched.append(body)
        return {"ok": True, "data": []}

    with pytest.raises(ValueError, match="census_state.json"):
        census.run(limit=10, budget_seconds=60, fetch=fetch,
                   leaderboard=[_row("0x" + "b" * 40, 1_000_000, 5_000_000)])

    assert census.STATE.read_text() == damaged
    assert not census.CENSUS_FILE.exists()
    assert fetched == []                                  # and nothing was read from Hyperliquid
