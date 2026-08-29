# tests/test_chain_collect.py
import json

import pytest

from src.chain import collect
from src.chain.budget import CallBudget
from src.chain.pagination import WalkResult

ARB = {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0}
BASE = {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 1}


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Every sweep test except the skip test needs a key present.

    Without this, sweep_wallet takes the skipped_no_api_key path on any machine
    that has not exported one, and the whole module passes vacuously.
    """
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key")


def budget(calls=100):
    return CallBudget(max_calls=calls, seconds=1000, clock=lambda: 0.0)


def erc20_row(value="5000000000000", to="0xdest", frm="0xtarget", block="100",
              ts="1781000000", h="0xhash1", log="0", symbol="USDC"):
    return {"blockNumber": block, "timeStamp": ts, "hash": h, "logIndex": log,
            "from": frm, "to": to, "value": value, "tokenSymbol": symbol,
            "tokenDecimal": "6", "contractAddress": "0xaf88"}


def test_normalise_row_produces_the_phase_one_record():
    rec = collect.normalise_row(erc20_row(), ARB, "erc20", lambda s, d: None)
    assert rec["id"] == "arbitrum:0xhash1:erc20:0"
    assert rec["chain"] == "arbitrum" and rec["chain_id"] == 42161
    assert rec["src"] == "0xtarget" and rec["dst"] == "0xdest"
    assert rec["kind"] == "erc20" and rec["asset"] == "USDC"
    assert rec["amount"] == 5_000_000.0
    assert rec["amount_usd"] == 5_000_000.0
    assert rec["value_basis"] == "stable_par"
    assert rec["block"] == 100 and rec["ts"] == 1781000000
    assert rec["timestamp"].startswith("2026-")


def test_normalise_row_drops_self_transfers_and_rows_without_both_sides():
    assert collect.normalise_row(erc20_row(to="0xtarget"), ARB, "erc20", lambda s, d: None) is None
    assert collect.normalise_row(erc20_row(to=""), ARB, "erc20", lambda s, d: None) is None


def test_normalise_row_uses_eighteen_decimals_for_native_transfers():
    row = {"blockNumber": "1", "timeStamp": "1781000000", "hash": "0xh",
           "from": "0xa", "to": "0xb", "value": "1000000000000000000"}
    rec = collect.normalise_row(row, ARB, "native", lambda s, d: 2000.0)
    assert rec["amount"] == 1.0
    assert rec["asset"] == "ETH"
    assert rec["amount_usd"] == 2000.0
    assert rec["value_basis"] == "daily_close"


def test_sweep_writes_records_marks_spam_and_advances_the_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    poison = "0x1419b0d742da87d053373018740e7c3a41402d5f"
    real = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        if kind != "erc20":
            return WalkResult([], start, 1, False, []), None
        return WalkResult([
            erc20_row(h="0xreal", to=real, value="13000000000000"),
            erc20_row(h="0xpoison", to=poison, value="0", log="1"),
        ], 100, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["records"] == 1
    assert result["chains"]["arbitrum"]["spam"] == 1
    assert result["chains"]["arbitrum"]["cursor"] == 100

    written = json.loads(next((tmp_path / "transfers" / "arbitrum").glob("*.json")).read_text())
    assert [r["dst"] for r in written] == [real]

    rolled = json.loads((tmp_path / "transfers_spam" / "latest.json").read_text())
    assert rolled["entries"][0]["address"] == poison
    assert rolled["entries"][0]["mimics"] == real

    cursors = json.loads((tmp_path / "state" / "transfer_cursors.json").read_text())
    assert cursors["arbitrum:0xtarget:erc20"] == 100


def test_a_failed_chain_is_recorded_as_degraded_not_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []),
                                                     "Max rate limit reached"))

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["error"] == "Max rate limit reached"
    assert "arbitrum" in result["degraded_sources"]


def test_an_empty_sweep_and_a_failed_sweep_do_not_serialise_identically(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), None))
    healthy = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), "boom"))
    failed = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert healthy != failed
    assert healthy["degraded_sources"] == []
    assert failed["degraded_sources"] == ["arbitrum"]


def test_a_non_cluster_wallet_is_probed_before_being_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    swept = []
    monkeypatch.setattr(collect, "probe_activity",
                        lambda a, c, b: (c["name"] == "arbitrum", None))

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        swept.append(chain["name"])
        return WalkResult([], start, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xfrontier", [ARB, BASE], budget(), cluster=False)

    assert set(swept) == {"arbitrum"}
    assert result["chains"]["base"]["probed_inactive"] is True
    assert result["chains"]["base"]["records"] == 0
    assert result["degraded_sources"] == []


def test_a_failed_probe_degrades_the_chain_rather_than_calling_it_inactive(
        tmp_path, monkeypatch):
    """A probe that could not read must never be recorded as an empty chain."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "probe_activity",
                        lambda a, c, b: (False, "Max rate limit reached"))
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda *a, **k: pytest.fail("must not sweep after a failed probe"))

    result = collect.sweep_wallet("0xfrontier", [BASE], budget(), cluster=False)

    assert result["chains"]["base"]["error"] == "Max rate limit reached"
    assert result["chains"]["base"]["probed_inactive"] is False
    assert result["degraded_sources"] == ["base"]


def test_a_cluster_wallet_is_never_probed(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    def boom(*a, **k):
        raise AssertionError("cluster wallets must be swept unconditionally")

    monkeypatch.setattr(collect, "probe_activity", boom)
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []), None))

    collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)


def test_a_partial_sweep_persists_what_it_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "fetch_kind", lambda a, c, k, s, b, **kw: (
        WalkResult([erc20_row(h="0xkept", value="13000000000000")], 100, 1, True, []),
        "budget_exhausted:call_budget"))

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["records"] == 1
    assert result["chains"]["arbitrum"]["truncated"] is True
    assert "arbitrum" in result["degraded_sources"]
    written = json.loads(next((tmp_path / "transfers" / "arbitrum").glob("*.json")).read_text())
    assert len(written) == 1


def test_records_for_reads_every_chain_and_excludes_spam(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    for chain in ("arbitrum", "base"):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([
            {"id": f"{chain}:a", "chain": chain, "src": "0xtarget", "dst": "0xreal",
             "amount_usd": 100.0, "ts": 1, "spam": False},
            {"id": f"{chain}:b", "chain": chain, "src": "0xtarget", "dst": "0xpoison",
             "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"},
            {"id": f"{chain}:c", "chain": chain, "src": "0xstranger", "dst": "0xother",
             "amount_usd": 50.0, "ts": 3, "spam": False},
        ]))

    got = collect.records_for("0xTARGET")
    assert {r["chain"] for r in got} == {"arbitrum", "base"}
    assert all(r["dst"] == "0xreal" for r in got)
    assert len(got) == 2


def test_records_for_can_include_spam_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xpoison",
         "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"}]))
    assert collect.records_for("0xtarget") == []
    assert len(collect.records_for("0xtarget", include_spam=True)) == 1


def test_records_for_returns_empty_when_nothing_has_been_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "nothing-here")
    assert collect.records_for("0xtarget") == []


def test_sweep_is_skipped_without_an_api_key(tmp_path, monkeypatch):
    """Matches the existing expand_frontier pattern: no key is a named skip, not
    a stream of Invalid API Key errors that burn the whole budget."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda *a, **k: pytest.fail("must not call the API"))

    b = budget()
    result = collect.sweep_wallet("0xtarget", [ARB], b, cluster=True)
    assert result["status"] == "skipped_no_api_key"
    assert result["degraded_sources"] == ["arbitrum"]
    assert b.calls_used == 0


def test_a_price_unavailable_major_reaches_transfers_dir_not_the_spam_rollup(
        tmp_path, monkeypatch):
    """A known major (ETH) the price source could not price today must survive
    as a real, readable record -- not vanish into an address-keyed spam count.
    price_lookup returning None here is exactly sweep_wallet's own default
    behaviour when no price_lookup is supplied, which is how Tasks 11/12 call
    it in production."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    def native_row(h="0xeth"):
        return {"blockNumber": "50", "timeStamp": "1781000000", "hash": h,
                "from": "0xtarget", "to": "0xdest", "value": "2000000000000000000"}

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        if kind != "native":
            return WalkResult([], start, 1, False, []), None
        return WalkResult([native_row()], 50, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True,
                                  price_lookup=lambda s, d: None)

    chain = result["chains"]["arbitrum"]
    assert chain["records"] == 1
    assert chain["spam"] == 0
    assert chain["unpriced"] == 1

    got = collect.records_for("0xtarget")
    assert len(got) == 1
    assert got[0]["asset"] == "ETH"
    assert got[0]["amount_usd"] is None
    assert got[0]["value_basis"] == "price_unavailable"


def test_a_finished_chains_cursor_is_durable_before_the_next_chain_is_swept(
        tmp_path, monkeypatch):
    """write_cursors must flush after each chain, not once at the very end --
    otherwise a process killed mid-sweep (the 10-minute CI timeout budget.py
    is built around) loses cursor progress for chains that had already
    finished and already written their records, and the spam rollup's
    straight-addition merge would inflate suppressed counts on every retry
    of a range that was already accounted for."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")

    seen = {}

    def fake_fetch_kind(address, chain, kind, start, b, **kw):
        if chain["name"] == "base" and kind == "erc20":
            # By the time base's erc20 kind is being fetched, arbitrum must
            # already be durable on disk -- read straight from CURSOR_PATH,
            # not from any in-memory state, to prove it.
            seen.update(collect.read_cursors())
        if kind != "erc20":
            return WalkResult([], start, 1, False, []), None
        return WalkResult([], 100, 1, False, []), None

    monkeypatch.setattr(collect, "fetch_kind", fake_fetch_kind)

    collect.sweep_wallet("0xtarget", [ARB, BASE], budget(), cluster=True)

    assert seen.get("arbitrum:0xtarget:erc20") == 100


def test_multiple_failed_kinds_are_all_recorded_not_just_the_last(tmp_path, monkeypatch):
    """chain_result["error"] is overwritten per kind, so if erc20 and native
    both fail only the later message would survive there -- errors_by_kind
    keeps every kind's own message instead of losing all but the last."""
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "SPAM_DIR", tmp_path / "transfers_spam")
    monkeypatch.setattr(collect, "CURSOR_PATH", tmp_path / "state" / "transfer_cursors.json")
    monkeypatch.setattr(collect, "fetch_kind",
                        lambda a, c, k, s, b, **kw: (WalkResult([], s, 1, False, []),
                                                     f"{k} failed"))

    result = collect.sweep_wallet("0xtarget", [ARB], budget(), cluster=True)

    assert result["chains"]["arbitrum"]["errors_by_kind"] == {
        "erc20": "erc20 failed", "native": "native failed", "internal": "internal failed"}


def test_sweep_health_summarises_across_wallets():
    results = [
        {"address": "0xa", "chains": {"arbitrum": {"records": 3, "spam": 5, "calls": 2,
                                                   "cursor": 10, "gaps": [], "truncated": False,
                                                   "error": None, "probed_inactive": False}},
         "degraded_sources": []},
        {"address": "0xb", "chains": {"arbitrum": {"records": 1, "spam": 0, "calls": 1,
                                                   "cursor": 12, "gaps": [7], "truncated": False,
                                                   "error": "boom", "probed_inactive": False}},
         "degraded_sources": ["arbitrum"]},
    ]
    health = collect.sweep_health(results)
    assert health["records"] == 4
    assert health["spam_suppressed"] == 5
    assert health["calls"] == 3
    assert health["degraded_sources"] == ["arbitrum"]
    assert health["possible_gaps"] == 1
    assert health["wallets"] == 2


def test_sweep_health_reports_unpriced_and_spam_by_reason():
    """A run where the price source is down must say so in the health output
    -- as a distinct `unpriced` total -- rather than only showing up as an
    unexplained spike in the flat `spam_suppressed` count."""
    results = [
        {"address": "0xa", "chains": {"arbitrum": {
            "records": 2, "spam": 3, "calls": 2, "cursor": 10, "gaps": [],
            "truncated": False, "error": None, "probed_inactive": False,
            "unpriced": 1, "spam_by_reason": {"dust": 2, "lookalike": 1}}},
         "degraded_sources": []},
        {"address": "0xb", "chains": {"arbitrum": {
            "records": 1, "spam": 1, "calls": 1, "cursor": 12, "gaps": [],
            "truncated": False, "error": None, "probed_inactive": False,
            "unpriced": 2, "spam_by_reason": {"dust": 1}}},
         "degraded_sources": []},
    ]
    health = collect.sweep_health(results)
    assert health["unpriced"] == 3
    assert health["spam_by_reason"] == {"dust": 3, "lookalike": 1}
