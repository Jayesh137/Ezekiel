# tests/test_solana_watch.py
from src import solana_watch as sw

ADDR = "2xm4bb8KmpafeC2Zcb37J7UFNcLfmKvaZmyhYKhRtVSv"


def test_new_since_stops_at_the_last_seen_signature():
    rows = [{"signature": "s3"}, {"signature": "s2"}, {"signature": "s1"}]
    assert [r["signature"] for r in sw.new_since(rows, "s1")] == ["s3", "s2"]
    assert sw.new_since(rows, "s3") == []
    assert [r["signature"] for r in sw.new_since(rows, None)] == ["s3", "s2", "s1"]


def test_report_names_failures_and_first_runs():
    addresses = {ADDR: {"role": "cluster", "provenance": "cctp"}, "other": {"role": "lead"}}
    readings = {ADDR: ([{"signature": "s9", "blockTime": 1787280924},
                        {"signature": "s8", "blockTime": 1787280853}], None),
                "other": ([], "timeout")}
    report = sw.build_report(addresses, readings, {ADDR: "s8"})
    by = {w["address"]: w for w in report["wallets"]}
    assert by[ADDR]["new_signatures"] == ["s9"] and by[ADDR]["first_run"] is False
    assert by[ADDR]["last_activity"].startswith("2026-08-21")
    assert by["other"]["read_ok"] is False and by["other"]["new_signatures"] == []


def test_fetch_reports_rpc_errors():
    class R:
        def json(self):
            return {"jsonrpc": "2.0", "error": {"code": -32005, "message": "rate limited"}}

    rows, err = sw.fetch_signatures(ADDR, post=lambda *a, **k: R())
    assert rows == [] and "rate limited" in err
