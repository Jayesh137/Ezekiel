# tests/test_watch_new_payees.py
"""A wallet of his paying an address no cluster wallet has ever touched.

The target's L1 outbound alerts through the tracer and every cluster wallet's
HL-native send through the explorer, but his OTHER L1 wallets — the treasury and
`0xf078969e…`, where his money sits on-chain — had no tripwire: a fresh wallet
funded from either reached the roster only as an INFO node that never buzzes.
Measured over their history, new EOA payees are rare (the treasury: none in six
new destinations; `0xf078969e…`: 10 EOAs of 181), so each one is news.
"""

import scripts.check_watchlist as check
from src import watchlist as wl

HIS = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
T = "0x45d26f28196d226497130c4bac709d808fed4029"
FRESH = "0x" + "fe" * 20
ROUTER = "0x" + "c0" * 20
OLD = "0x" + "01" * 20

QUIET = {"is_contract": False, "txs": 2, "token_transfers": 1}


def rec(dst, *, kind="erc20", usd=500_000.0, amount=500_000.0, src=HIS, spam=False, ts=1789600000):
    return {"src": src, "dst": dst, "kind": kind, "amount_usd": usd, "amount": amount,
            "chain": "arbitrum", "asset": "ETH" if kind != "erc20" else "USDC",
            "ts": ts, "tx_hash": "0xabc", "spam": spam}


def test_native_gas_counts_at_any_amount_and_tokens_need_real_value():
    rows = [rec(FRESH, kind="native", usd=None, amount=0.01),       # gas to a fresh wallet
            rec("0x" + "02" * 20, usd=50.0, amount=50.0),            # token dust
            rec("0x" + "03" * 20, usd=None, amount=10.0),            # unpriced token
            rec("0x" + "04" * 20, spam=True),                        # poisoning
            rec(HIS, src="0x" + "05" * 20),                          # inbound
            rec("0x" + "06" * 20, kind="native", usd=0.0, amount=0)]  # zero native
    got = wl.outbound_payments(rows, HIS)
    assert set(got) == {FRESH}
    assert got[FRESH]["unvalued"] == 1


def test_novel_excludes_the_seen_and_the_cluster():
    payments = wl.outbound_payments([rec(FRESH), rec(OLD), rec(T)], HIS)
    assert set(wl.novel_payments(payments, {OLD}, {T, HIS})) == {FRESH}


def test_severity_is_critical_only_when_hyperliquid_shows_the_address_in_use():
    assert wl.novelty_severity({"read_ok": True, "account_value": "12.5", "fills": 0}) == "CRITICAL"
    assert wl.novelty_severity({"read_ok": True, "account_value": "0.0", "fills": 3}) == "CRITICAL"
    assert wl.novelty_severity({"read_ok": True, "account_value": "0.0", "fills": 0}) == "HIGH"
    assert wl.novelty_severity({"read_ok": False}) == "HIGH"
    assert wl.novelty_severity(None) == "HIGH"


class FakeReadings:
    def __init__(self, table):
        self.table = table

    def for_address(self, address, chains):
        got = self.table.get(address)
        return [got] if got else []


def _run(rows, table, alert_result=True):
    sent = []
    held = check.check_new_payees(
        HIS, {OLD}, {T, HIS}, FakeReadings(table), records=rows,
        hl_reader=lambda addr: {"read_ok": True, "account_value": "0", "fills": 0},
        alert=lambda wallet, dst, payment, state, severity:
            sent.append((dst, severity)) or alert_result)
    return held, sent


def test_a_quiet_new_payee_alerts_and_is_released():
    held, sent = _run([rec(FRESH, kind="native", usd=None, amount=0.01)], {FRESH: QUIET})
    assert sent == [(FRESH, "HIGH")]
    assert held == set()


def test_a_contract_or_busy_payee_is_infrastructure_not_news():
    held, sent = _run([rec(ROUTER)], {ROUTER: {"is_contract": True, "txs": 9}})
    assert sent == [] and held == set()
    held, sent = _run([rec(ROUTER)], {ROUTER: {"is_contract": False, "txs": 3_000_000}})
    assert sent == [] and held == set()


def test_an_unmeasured_payee_is_held_for_next_run_not_asserted():
    held, sent = _run([rec(FRESH)], {})
    assert sent == [] and held == {FRESH}


def test_an_undelivered_alert_is_held_so_it_is_retried():
    held, sent = _run([rec(FRESH)], {FRESH: QUIET}, alert_result=False)
    assert sent and held == {FRESH}


def test_an_old_payee_is_never_news():
    held, sent = _run([rec(OLD)], {OLD: QUIET})
    assert sent == [] and held == set()


def _main(monkeypatch, doc, records):
    saved = []
    monkeypatch.setattr(check, "load_config", lambda: {
        "target_wallet": T, "known_self_wallets": [HIS], "watch_wallets": [HIS]})
    monkeypatch.setattr(check, "_roster", lambda: {})
    monkeypatch.setattr(check, "target_account_value", lambda cfg: 1.0)
    monkeypatch.setattr(check, "read_wallet", lambda address, config, target_value=None:
                        (check.snapshot(address, account_value=1.0), []))
    monkeypatch.setattr(check, "sweep", lambda addr, cfg, **kw: None)
    monkeypatch.setattr(check, "target_world", lambda cfg: {})
    monkeypatch.setattr(check, "_previous", lambda: {})
    monkeypatch.setattr(check, "_previous_doc", lambda: doc)
    monkeypatch.setattr(check, "busy_flags", lambda hits: {})
    monkeypatch.setattr(check, "records_for", lambda addr: records.get(addr, []))
    monkeypatch.setattr(check, "save", lambda report: saved.append(report))
    return saved


def test_the_first_run_seeds_history_and_alerts_nothing(monkeypatch):
    fired = []
    monkeypatch.setattr(check, "check_new_payees",
                        lambda *a, **k: fired.append(a) or set())
    saved = _main(monkeypatch, {}, {HIS: [rec(OLD), rec(FRESH)], T: [rec(ROUTER, src=T)]})
    assert check.main() == 0
    assert fired == [], "a first reading checked history for news"
    assert {OLD, FRESH, ROUTER} <= set(saved[0]["l1_seen"])


def test_a_later_run_checks_settled_wallets_and_keeps_held_payees_unseen(monkeypatch):
    monkeypatch.setattr(check, "check_new_payees", lambda address, seen, cluster, readings: {FRESH})
    saved = _main(monkeypatch, {"l1_seen": [OLD]}, {HIS: [rec(OLD), rec(FRESH)]})
    assert check.main() == 0
    assert FRESH not in saved[0]["l1_seen"], "a held payee was marked seen and would never be retried"
    assert saved[0]["l1_pending"] == [FRESH]
