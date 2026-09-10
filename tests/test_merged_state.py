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
