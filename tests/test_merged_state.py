# tests/test_merged_state.py
"""Candidate perp state must include the HIP-3 books, where the rarest
positions live, and must say which dex it could not read."""

from src import scanner


def test_hip3_positions_are_merged_into_one_state():
    def fetch(body):
        if body.get("dex") == "xyz":
            return {"assetPositions": [{"position": {"coin": "xyz:SP500", "szi": "1"}}]}
        if body.get("dex") == "dead":
            raise RuntimeError("no such dex")
        return {"marginSummary": {"accountValue": "5"},
                "assetPositions": [{"position": {"coin": "BTC", "szi": "-1"}}]}

    got = scanner.merged_clearinghouse_state("0xabc", dexes=["xyz", "dead"], fetch=fetch)
    coins = [p["position"]["coin"] for p in got["assetPositions"]]
    assert coins == ["BTC", "xyz:SP500"]
    assert got["marginSummary"]["accountValue"] == "5"
    assert got["unreadable_dexes"] == ["dead"]


def test_an_unreadable_base_state_is_empty_not_partial():
    def fetch(body):
        raise RuntimeError("down")
    assert scanner.merged_clearinghouse_state("0xabc", dexes=["xyz"], fetch=fetch) == {}


def test_live_dexes_prefer_the_venue_and_never_drop_a_configured_one(monkeypatch):
    """config.hip3_dexes names the one dex he is known to trade; ten exist."""
    from src import scanner

    monkeypatch.setattr(scanner, "_LIVE_DEXES", None)
    monkeypatch.setattr(scanner, "load_config", lambda: {"hip3_dexes": ["xyz", "private"]})
    got = scanner.live_hip3_dexes(
        fetch=lambda body: [{"name": "xyz"}, {"name": "flx"}, None, {"name": "io"}],
        refresh=True)
    assert got == ["xyz", "flx", "io", "private"]


def test_an_unreadable_venue_falls_back_to_the_configured_list(monkeypatch):
    from src import scanner

    monkeypatch.setattr(scanner, "_LIVE_DEXES", None)
    monkeypatch.setattr(scanner, "load_config", lambda: {"hip3_dexes": ["xyz"]})

    def boom(body):
        raise RuntimeError("down")

    assert scanner.live_hip3_dexes(fetch=boom, refresh=True) == ["xyz"]


def test_the_dex_list_is_cached_for_the_process(monkeypatch):
    from src import scanner

    calls = []
    monkeypatch.setattr(scanner, "_LIVE_DEXES", None)
    monkeypatch.setattr(scanner, "load_config", lambda: {"hip3_dexes": []})

    def fetch(body):
        calls.append(body)
        return [{"name": "xyz"}]

    scanner.live_hip3_dexes(fetch=fetch, refresh=True)
    scanner.live_hip3_dexes(fetch=fetch)
    assert len(calls) == 1
