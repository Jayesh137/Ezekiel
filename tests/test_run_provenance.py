"""The trace.yml provenance step, every reader injected."""

import json

import pytest

import scripts.run_provenance as rp
from src import utils
from src.boundary import bridge2

T = "0x45d26f28196d226497130c4bac709d808fed4029"
S = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
SINK = "0x" + "a" * 40
NEW, OTHER = "0x" + "5" * 40, "0x" + "6" * 40


def dep(depositor, usd, block, tx):
    return {"address": bridge2.USDC, "topics": [bridge2.TOPIC_TRANSFER, bridge2.topic_address(depositor),
                                                 bridge2.topic_address(bridge2.BRIDGE)],
            "data": "0x" + format(int(usd * 1e6), "064x"), "blockNumber": hex(block),
            "timeStamp": hex(1_790_000_000 + block), "logIndex": "0x0", "transactionHash": tx}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "load_config", lambda: {"target_wallet": T, "known_self_wallets": []})
    (tmp_path / "perimeter").mkdir()
    (tmp_path / "perimeter" / "latest.json").write_text(json.dumps({"members": {
        T: {"address": T, "role": "core", "weight": 1.0, "why": "config"},
        SINK: {"address": SINK, "role": "sink", "weight": 0.6, "why": "holds his money"},
        S: {"address": S, "role": "deposit", "weight": 1.0, "why": "deposit"}}}))
    sent = {"prov": [], "boundary": []}
    monkeypatch.setattr("src.alerts.alert_provenance_hit", lambda row: sent["prov"].append(row) or True)
    monkeypatch.setattr("src.alerts.alert_boundary_finding", lambda row: sent["boundary"].append(row) or True)
    return tmp_path, sent


def readers(deposits=(), ledgers=None, inbound=None, head=1_000):
    def ledger(a):
        return (ledgers or {}).get(a, [])

    def read_inbound(chain, address, *, since_ts, until_ts):
        return [{"from": f, "usd": u, "ts": until_ts - 5, "chain": chain, "from_is_contract": False,
                 "from_is_scam": False, "token": "0xusdc", "symbol": "USDC", "tx_hash": "0x1"}
                for f, u in (inbound or {}).get(address, [])]
    return {"head": lambda: head,
            "deposits": lambda lo, hi: [d for d in deposits if lo <= int(d["blockNumber"], 16) <= hi],
            "ledger": ledger, "unit": lambda a: {"addresses": [], "operations": []},
            "inbound": read_inbound, "first_gas": lambda chain, a: None,
            "circle_source": lambda entry, account: None,
            "label_of": lambda a, chain: None}


def _deposit_row(usd, ts_ms):
    return [{"time": ts_ms, "hash": "0xh", "delta": {"type": "deposit", "usdc": str(usd)}}]


def test_a_new_large_deposit_funded_by_his_wallet_pages_critical(sandbox):
    tmp, sent = sandbox
    r = readers(deposits=[dep(NEW, 2e6, 900, "0xd1")],
                ledgers={NEW: _deposit_row(2e6, 1_790_000_900_000)}, inbound={NEW: [(T, 2e6)]})
    assert rp.main([], readers=r, now="2026-10-06T00:00:00+00:00") == 0
    st = json.loads((tmp / "provenance" / "latest.json").read_text())
    assert st["resolved"] == 1 and st["findings"][0]["severity"] == "CRITICAL"
    assert sent["prov"] and sent["prov"][0]["account"] == NEW
    pool = json.loads((tmp / "provenance" / "bridge_deposits.json").read_text())
    assert pool["deposits"][0]["wallet"] == NEW and pool["cursor"] == 1_000


def test_a_perimeter_member_depositing_into_hl_is_a_member_finding(sandbox):
    tmp, sent = sandbox
    rp.main([], readers=readers(deposits=[dep(SINK, 2e5, 800, "0xd2")]),
            now="2026-10-06T00:00:00+00:00")
    st = json.loads((tmp / "provenance" / "latest.json").read_text())
    assert st["member_findings"][0]["kind"] == "perimeter_member_active_on_hl"
    assert sent["boundary"]


def test_a_queued_account_is_not_re_resolved_inside_its_ttl(sandbox):
    tmp, sent = sandbox
    r = readers(deposits=[dep(OTHER, 5e5, 900, "0xd3")],
                ledgers={OTHER: _deposit_row(5e5, 1_790_000_900_000)}, inbound={OTHER: []})
    rp.main([], readers=r, now="2026-10-06T00:00:00+00:00")
    (tmp / "roster").mkdir()
    (tmp / "roster" / "latest.json").write_text(json.dumps({"wallets": [
        {"wallet": OTHER, "tier": "POSSIBLE"}]}))
    calls = []
    r2 = readers(head=1_100)
    r2["ledger"] = lambda a: calls.append(a) or []
    rp.main([], readers=r2, now="2026-10-06T01:00:00+00:00")
    assert calls == []
    rp.main([], readers=r2, now="2026-10-14T01:00:00+00:00")
    assert calls == [OTHER]


def test_dry_run_writes_only_its_directory(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    out = tmp_path_factory.mktemp("dry")
    rp.main(["--dry-run", str(out)],
            readers=readers(deposits=[dep(NEW, 2e6, 900, "0xd1")],
                            ledgers={NEW: _deposit_row(2e6, 1_790_000_900_000)},
                            inbound={NEW: [(T, 2e6)]}), now="2026-10-06T00:00:00+00:00")
    assert (out / "latest.json").exists() and not (tmp / "provenance").exists()
    assert not sent["prov"]


def test_a_dry_run_can_read_a_perimeter_from_anywhere(sandbox, tmp_path_factory):
    tmp, sent = sandbox
    other = tmp_path_factory.mktemp("per") / "latest.json"
    other.write_text(json.dumps({"members": {
        OTHER: {"address": OTHER, "role": "core", "weight": 1.0, "why": "test"}}}))
    out = tmp_path_factory.mktemp("dry")
    r = readers(deposits=[dep(NEW, 2e6, 900, "0xd1")],
                ledgers={NEW: _deposit_row(2e6, 1_790_000_900_000)}, inbound={NEW: [(OTHER, 2e6)]})
    rp.main(["--dry-run", str(out), "--perimeter", str(other)], readers=r,
            now="2026-10-06T00:00:00+00:00")
    st = json.loads((out / "latest.json").read_text())
    assert st["findings"] and st["findings"][0]["member"] == OTHER


def test_an_account_that_hit_a_transient_failure_is_retried_inside_its_ttl(sandbox):
    tmp, sent = sandbox
    r = readers(deposits=[dep(OTHER, 5e5, 900, "0xd3")],
                ledgers={OTHER: _deposit_row(5e5, 1_790_000_900_000)})

    def throttled(chain, address, *, since_ts, until_ts):
        from src.boundary.readers import ReadError
        raise ReadError("429")
    r["inbound"] = throttled
    rp.main([], readers=r, now="2026-10-06T00:00:00+00:00")
    (tmp / "roster").mkdir()
    (tmp / "roster" / "latest.json").write_text(json.dumps({"wallets": [
        {"wallet": OTHER, "tier": "POSSIBLE"}]}))
    calls = []
    r2 = readers(head=1_100, ledgers={OTHER: _deposit_row(5e5, 1_790_000_900_000)},
                 inbound={OTHER: []})
    r2["ledger"] = lambda a: calls.append(a) or _deposit_row(5e5, 1_790_000_900_000)
    rp.main([], readers=r2, now="2026-10-06T01:00:00+00:00")
    assert calls == [OTHER]


def test_a_slow_run_stops_starting_accounts_at_its_deadline(sandbox):
    # trace.yml gives the step 360s; the 2026-10-06 dry run spent 216s on one
    # account. Accounts not reached stay queued for the next run.
    tmp, sent = sandbox
    now = [0.0]
    accounts = ["0x" + c * 40 for c in "789bcdef"]
    r = readers(deposits=[dep(a, 5e5, 900 + i, f"0xd{i}") for i, a in enumerate(accounts)],
                inbound={a: [] for a in accounts})

    def slow_ledger(a):
        now[0] += 60.0
        return _deposit_row(5e5, 1_790_000_900_000)
    r["ledger"] = slow_ledger
    rp.main([], readers=r, now="2026-10-06T00:00:00+00:00", clock=lambda: now[0])
    st = json.loads((tmp / "provenance" / "latest.json").read_text())
    assert 0 < st["attempted"] < len(accounts)
    assert now[0] <= rp.RUN_SECONDS + 60.0
    assert st["deferred_accounts"] == len(accounts) - st["attempted"]
