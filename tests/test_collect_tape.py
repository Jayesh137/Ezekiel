"""The tape report and candidate registration are well-formed and safe."""

from scripts import collect_tape as ct


def _trade(h, buyer, seller, side, coin, sz, t):
    return {"hash": h, "users": [buyer, seller], "side": side, "coin": coin,
            "px": "4.0", "sz": str(sz), "time": t}


def _burst(wallet, coin, clip, n):
    other = "0x" + "9" * 40
    return [_trade(i, other, wallet, "A", coin, clip, i * 1750) for i in range(n)]


def test_report_counts_and_flags_a_live_program_hit():
    W = "0x" + "1" * 40
    report = ct.build_report(_burst(W, "NEAR", 250, 40), {"NEAR": {"size": 250}},
                             seconds=150, markets=["NEAR"])
    assert report["hit_count"] == 1
    assert report["program_hits"][0]["wallet"] == W
    assert report["trades_seen"] == 40


def test_no_trades_is_an_empty_report_not_a_crash():
    report = ct.build_report([], {"NEAR": {"size": 250}}, seconds=150, markets=["NEAR"])
    assert report["hit_count"] == 0 and report["trades_seen"] == 0


def test_register_hits_makes_each_a_candidate(tmp_path):
    from src.candidate_registry import iter_candidates
    W = "0x1111111111111111111111111111111111111111"
    ct.register_hits([{"wallet": W, "coin": "NEAR", "clip": 250, "orders": 40}], data_dir=tmp_path)
    rows = iter_candidates(tmp_path)
    assert [r["wallet"] for r in rows] == [W]
    assert "execution_program_tape" in rows[0]["discovery_sources"]


def test_register_hits_ignores_malformed_wallets(tmp_path):
    from src.candidate_registry import iter_candidates
    ct.register_hits([{"wallet": "nope", "coin": "NEAR"}], data_dir=tmp_path)
    assert iter_candidates(tmp_path) == []
