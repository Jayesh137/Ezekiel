# tests/test_withdrawals.py
"""A withdrawal is paired with the bridge payout of the same amount minutes
later; an unpaired one went to an address the system does not sweep."""

from src import withdrawals as wd

BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"
T = "0x45d26f28196d226497130c4bac709d808fed4029"


def _w(h, t_ms, usdc, fee="1.0"):
    return {"time": t_ms, "hash": h, "delta": {"type": "withdraw", "usdc": str(usdc), "fee": fee}}


def test_unique_withdrawals_dedupe_and_net_the_fee():
    ledger = [_w("0xa", 1_000_000, "1199999.0"), _w("0xa", 1_000_000, "1199999.0"),
              _w("0xb", 2_000_000, "500.0", fee="1.0"),
              {"time": 3, "hash": "0xc", "delta": {"type": "deposit", "usdc": "5"}}]
    got = wd.unique_withdrawals(ledger)
    assert [(w["hash"], w["net_usd"]) for w in got] == [("0xa", 1199998.0), ("0xb", 499.0)]


def test_matching_pairs_each_withdrawal_once():
    withdrawals = [{"hash": "0xa", "time_s": 1000, "net_usd": 1199998.0, "gross_usd": 1199999.0},
                   {"hash": "0xb", "time_s": 2000, "net_usd": 1199998.0, "gross_usd": 1199999.0},
                   {"hash": "0xc", "time_s": 3000, "net_usd": 50.0, "gross_usd": 51.0}]
    inbound = [{"ts": 993, "usd": 1199998.0, "tx_hash": "0xp1"},     # 7s BEFORE the ledger stamp
               {"ts": 2300, "usd": 1199998.5, "tx_hash": "0xp2"}]
    m = wd.match(withdrawals, inbound, now_s=100_000)
    assert [x["payout_tx"] for x in m["matched"]] == ["0xp1", "0xp2"]
    assert [x["hash"] for x in m["unmatched"]] == ["0xc"]
    assert m["settling"] == []


def test_a_fresh_withdrawal_is_settling_not_foreign():
    withdrawals = [{"hash": "0xa", "time_s": 5000, "net_usd": 10.0, "gross_usd": 11.0}]
    m = wd.match(withdrawals, [], now_s=5000 + 600)
    assert m["settling"] and not m["unmatched"]


def test_find_payout_picks_the_bridge_transfer_of_that_amount():
    w = {"hash": "0xa", "time_s": 1000, "net_usd": 999998.0}
    rows = [{"from": BRIDGE, "to": "0xEARLY", "value": "999998000000", "timeStamp": "900", "hash": "0x1"},
            {"from": BRIDGE, "to": "0xRIGHT", "value": "999998000000", "timeStamp": "1300", "hash": "0x2"},
            {"from": BRIDGE, "to": "0xLATER", "value": "999998000000", "timeStamp": "1900", "hash": "0x3"},
            {"from": "0xnotbridge", "to": "0xX", "value": "999998000000", "timeStamp": "1100", "hash": "0x4"},
            {"from": BRIDGE, "to": "0xOTHER", "value": "5000000", "timeStamp": "1100", "hash": "0x5"}]
    hit = wd.find_payout(w, rows, BRIDGE)
    assert hit["destination"] == "0xright" and hit["delay_s"] == 300


def test_resolve_reports_failures_and_budget_honestly():
    unmatched = [{"hash": "0xa", "time_s": 1000, "net_usd": 5.0, "gross_usd": 6.0},
                 {"hash": "0xb", "time_s": 2000, "net_usd": 5.0, "gross_usd": 6.0},
                 {"hash": "0xc", "time_s": 3000, "net_usd": 5.0, "gross_usd": 6.0}]

    def fetch_window(t):
        if t == 1000:
            return [{"from": BRIDGE, "to": "0xDEST", "value": "5000000", "timeStamp": "1010", "hash": "0xp"}], None
        if t == 2000:
            return [], "rate limited"
        return [], None

    got = wd.resolve_destinations(unmatched, fetch_window, BRIDGE, max_lookups=2)
    assert got[0]["destination"] == "0xdest" and got[0]["status"] == "resolved"
    assert got[1]["destination"] is None and got[1]["status"].startswith("unreadable")
    assert got[2]["status"].startswith("pending")


def test_report_counts():
    withdrawals = [{"hash": "0xa", "time_s": 1000, "net_usd": 9.0, "gross_usd": 10.0}]
    report = wd.build_report(withdrawals, [], [{"hash": "0xa", "destination": "0xd",
                                                "status": "resolved"}], now_s=999_999)
    assert report["withdrawals"] == 1 and report["unmatched"] == 1
    assert report["foreign"][0]["destination"] == "0xd"
