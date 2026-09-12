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


# --- Circle/CCTP withdrawals: a spot send to 0x2000…0000 -------------------
#
# Shapes captured live 2026-09-12: the ledger row of the 2026-09-11 $7M send
# and the substrate row of the mint that landed at his own Arbitrum address
# nine seconds later, from the zero address, for the amount less $0.20.

ZERO = "0x0000000000000000000000000000000000000000"
SYS = "0x2000000000000000000000000000000000000000"
TR = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"


def _send(h, t_ms, amount, dest=SYS, token="USDC", user=T, fee="0.0"):
    return {"time": t_ms, "hash": h, "delta": {"type": "send", "user": user, "destination": dest,
                                                "sourceDex": "spot", "destinationDex": "spot",
                                                "token": token, "amount": str(amount),
                                                "usdcValue": str(amount), "fee": fee}}


def _mint(ts, usd, dst=T, chain="arbitrum", tx="0xm", asset="USDC", spam=False, priced=True):
    return {"chain": chain, "ts": ts, "src": ZERO, "dst": dst, "kind": "erc20", "asset": asset,
            "amount": usd, "amount_usd": usd if priced else None, "spam": spam, "tx_hash": tx}


def test_cctp_withdrawals_are_usdc_sends_to_the_system_address_by_the_wallet():
    ledger = [
        _send("0x159d", 1789096580307, 7000000),
        _send("0x159d", 1789096580307, 7000000),                 # stored twice: once
        _send("0xhype", 1789096580308, 1000, token="HYPE"),      # not USDC: no dollar value
        _send("0xelse", 1789096580309, 5000, dest=TR),           # a send to a person
        _send("0xin", 1789096580310, 5000, user=TR, dest=SYS),   # someone else's send
        {"time": 1, "hash": "0xw", "delta": {"type": "withdraw", "usdc": "5", "fee": "1"}},
    ]
    got = wd.cctp_withdrawals(ledger, T.upper())
    assert [(w["hash"], w["gross_usd"], w["net_usd"], w["route"]) for w in got] == [
        ("0x159d", 7000000.0, 7000000.0, "cctp")]
    assert got[0]["time_s"] == 1789096580


def test_cctp_inbound_is_a_priced_usdc_mint_at_a_cluster_address():
    records = [
        _mint(1789096589, 6999999.8, tx="0x3e83"),
        _mint(1789096590, 6999999.8, dst="0xstranger", tx="0xno1"),     # not his
        _mint(1789096591, 100.0, tx="0xno2", spam=True),                 # quarantined
        _mint(1789096592, 100.0, tx="0xno3", priced=False),              # unpriced: never $0
        _mint(1789096593, 100.0, tx="0xno4", asset="USDT"),
        {"chain": "arbitrum", "ts": 1789096594, "src": "0xbridge", "dst": T, "kind": "erc20",
         "asset": "USDC", "amount": 5.0, "amount_usd": 5.0, "spam": False, "tx_hash": "0xno5"},
        _mint(1789096595, 2.0, dst=TR, chain="ethereum", tx="0xtr"),
    ]
    got = wd.cctp_inbound(records, {T, TR.upper()})
    assert [(r["tx_hash"], r["chain"], r["wallet"]) for r in got] == [
        ("0x3e83", "arbitrum", T), ("0xtr", "ethereum", TR)]


def test_a_cctp_withdrawal_pairs_with_its_mint_despite_circles_fee():
    w = wd.cctp_withdrawals([_send("0x159d", 1789096580307, 7000000)], T)
    inbound = wd.cctp_inbound([_mint(1789096589, 6999999.8, tx="0x3e83")], {T})
    m = wd.match(w, inbound, now_s=1789096580 + 86400)
    assert [(x["hash"], x["payout_tx"], x["chain"], x["wallet"]) for x in m["matched"]] == [
        ("0x159d", "0x3e83", "arbitrum", T)]
    assert m["unmatched"] == [] and m["settling"] == []


def test_resolve_cctp_keeps_payload_foreign_and_unresolved_apart():
    unmatched = [{"hash": "0xa", "time_s": 1, "net_usd": 1.0, "gross_usd": 1.0, "route": "cctp"},
                 {"hash": "0xb", "time_s": 2, "net_usd": 2.0, "gross_usd": 2.0, "route": "cctp"},
                 {"hash": "0xc", "time_s": 3, "net_usd": 3.0, "gross_usd": 3.0, "route": "cctp"},
                 {"hash": "0xd", "time_s": 4, "net_usd": 4.0, "gross_usd": 4.0, "route": "cctp"}]
    payloads = {
        "0xa": {"destination": TR.upper(), "destination_chain": "base"},       # his, on Base
        "0xb": {"destination": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
                "destination_chain": "base"},                                   # a stranger
        "0xc": {"destination": "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv",
                "destination_chain": "solana"},                                 # his Solana wallet
        # 0xd: rolled out of the explorer window
    }
    r = wd.resolve_cctp(unmatched, payloads,
                        cluster={T, TR, "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"})
    assert [(x["hash"], x["chain"]) for x in r["by_payload"]] == [("0xa", "base"), ("0xc", "solana")]
    assert [(x["hash"], x["destination"]) for x in r["foreign"]] == [
        ("0xb", "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd")]
    assert [x["hash"] for x in r["unresolved"]] == ["0xd"]
    assert r["unresolved"][0]["destination"] is None
    assert "rolled out" in r["unresolved"][0]["status"]


def test_cctp_report_counts_every_bucket_and_never_settles_the_unresolved():
    w = wd.cctp_withdrawals([_send("0x1", 1_000_000, 100), _send("0x2", 2_000_000, 200),
                             _send("0x3", 3_000_000, 300), _send("0x4", 99_000_000, 400)], T)
    inbound = wd.cctp_inbound([_mint(1_100, 99.8, tx="0xm1")], {T})
    payloads = {"0x2": {"destination": TR, "destination_chain": "base"}}
    r = wd.cctp_report(w, inbound, payloads, {T, TR}, now_s=99_000 + 60)
    assert (r["withdrawals"], r["withdrawn_usd"]) == (4, 1000.0)
    assert r["matched_to_cluster_mint"] == 1 and r["landed"][0]["payout_tx"] == "0xm1"
    assert r["matched_by_payload"] == 1
    assert r["settling"] == 1                      # 0x4 is a minute old
    assert [x["hash"] for x in r["unresolved"]] == ["0x3"] and r["unresolved_usd"] == 300.0
    assert r["foreign"] == []


def test_cctp_lines_survive_an_empty_report_and_name_the_findings():
    assert wd.cctp_lines({})[0].startswith("[withdrawals] circle: 0 CCTP withdrawal(s)")
    assert wd.cctp_lines(None)
    r = {"withdrawals": 2, "withdrawn_usd": 9.0, "matched_to_cluster_mint": 0,
         "foreign": [{"net_usd": 4.0, "destination": "0xdead", "chain": None}],
         "unresolved": [{"net_usd": 5.0, "time_s": 7, "hash": "0xu", "status": "gone"}],
         "unresolved_usd": 5.0}
    lines = wd.cctp_lines(r)
    assert any("FOREIGN $4.00 -> 0xdead on an unknown chain" in ln for ln in lines)
    assert any("UNRESOLVED $5.00" in ln and "0xu" in ln for ln in lines)
