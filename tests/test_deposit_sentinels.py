# tests/test_deposit_sentinels.py
"""His private deposit addresses, read from their own side.

`linkage.py` reads address reuse only from SWEPT wallets' outbound, so a wallet
never swept could pay his deposit address for ever and link to nothing — and the
one outsider that ever did, `0xda0932d2…` ($249,993.84 into `0x8570c2ae…` on
2024-07-31, with a same-day $6 Hyperliquid deposit), appeared nowhere. The
deposit address's own sweep holds every sender; these tests pin reading it.
"""

import json

from src import deposit_sentinels as ds
from src import roster

T = "0x45d26f28196d226497130c4bac709d808fed4029"
TREASURY = "0x1419e75330c71ce463102e6a1eb62fe80b412d5f"
DEPOSIT = "0x8570c2aebf16ebe51690674cc7116dac6f0eb68e"
STRANGER = "0xda0932d2a880bafa82bc2ac41ab0caafc5544f52"
POISONER = "0xf0775c88ea4e58154f434c0773d760a42479719e"
HOT = "0x25681ab599b4e2ceea31f8b498052c53fc2d74db"
CLUSTER = {T, TREASURY}

QUIET = {"is_contract": False, "txs": 9, "token_transfers": 142}
BUSY = {"is_contract": False, "txs": 2_818_016, "token_transfers": 2_042_752}


def rec(src, dst, usd, ts=1722428300, chain="arbitrum", asset="USDC", spam=False):
    return {"src": src, "dst": dst, "amount_usd": usd, "ts": ts, "chain": chain,
            "asset": asset, "tx_hash": f"0x{ts:x}", "spam": spam}


# --- classification ---------------------------------------------------------------

def test_busy_anywhere_is_busy_and_unmeasured_is_never_quiet():
    assert ds.classify([QUIET]) == ds.CLASS_QUIET
    assert ds.classify([QUIET, BUSY]) == ds.CLASS_BUSY
    assert ds.classify([{"is_contract": True, "txs": 3}]) == ds.CLASS_CONTRACT
    assert ds.classify([]) == ds.CLASS_UNMEASURED
    assert ds.classify(None) == ds.CLASS_UNMEASURED


# --- which addresses are his deposit addresses ----------------------------------------

def test_candidates_come_from_the_graphs_forwarding_grades_and_the_inference_file():
    nodes = [{"wallet": DEPOSIT, "evidence": {"service_reason": "conduit: forwards 100%"}},
             {"wallet": HOT, "evidence": {"service_reason": "global activity: 2,818,016 txs"}}]
    inferred = {"addresses": {"0x" + "ab" * 20: {"entity": "deposit address forwarding"}}}
    got = ds.forwarding_candidates(nodes, inferred)
    assert set(got) == {DEPOSIT, "0x" + "ab" * 20}


def test_a_sentinel_needs_real_cluster_money_and_a_quiet_reading():
    candidates = {DEPOSIT: "conduit", "0x" + "01" * 20: "conduit",
                  "0x" + "02" * 20: "conduit", "0x" + "03" * 20: "conduit"}
    paid = {DEPOSIT: {"usd": 58_337_940.0, "chains": {"arbitrum"}},
            "0x" + "01" * 20: {"usd": 999.0, "chains": set()},          # too little
            "0x" + "02" * 20: {"usd": 5_000.0, "chains": set()},        # busy
            "0x" + "03" * 20: {"usd": 5_000.0, "chains": set()}}        # unmeasured
    classes = {DEPOSIT: ds.CLASS_QUIET, "0x" + "01" * 20: ds.CLASS_QUIET,
               "0x" + "02" * 20: ds.CLASS_BUSY}
    sentinels, pending, excluded = ds.select_sentinels(candidates, paid, classes, None, [])
    assert set(sentinels) == {DEPOSIT}
    assert set(pending) == {"0x" + "03" * 20}, "rule 9: unmeasured waits, it is never a sentinel"
    assert set(excluded) == {"0x" + "02" * 20}


def test_membership_survives_the_graph_forgetting_the_address():
    """A deposit address is a permanent fact; the graph that inferred it is not."""
    previous = {"sentinels": {DEPOSIT: {"reason": "conduit", "since": "2026-09-16T00:00:00+00:00"}}}
    sentinels, _, _ = ds.select_sentinels({}, {}, {DEPOSIT: ds.CLASS_QUIET}, previous, [])
    assert DEPOSIT in sentinels
    assert sentinels[DEPOSIT]["since"] == "2026-09-16T00:00:00+00:00"


def test_a_kept_sentinel_later_measured_busy_is_dropped():
    previous = {"sentinels": {DEPOSIT: {"reason": "conduit"}}}
    sentinels, _, excluded = ds.select_sentinels({}, {}, {DEPOSIT: ds.CLASS_BUSY}, previous, [])
    assert DEPOSIT not in sentinels and DEPOSIT in excluded


def test_an_operator_configured_sentinel_needs_no_inference():
    sentinels, _, _ = ds.select_sentinels({}, {}, {}, None, [DEPOSIT.upper()])
    assert DEPOSIT in sentinels


# --- who pays them ---------------------------------------------------------------------

def test_senders_ignore_the_cluster_dust_spam_and_the_address_itself():
    rows = [rec(T, DEPOSIT, 1_000_000.0), rec(TREASURY, DEPOSIT, 5_000_000.0),
            rec(STRANGER, DEPOSIT, 249_993.84),
            rec(POISONER, DEPOSIT, 0.0), rec("0x" + "77" * 20, DEPOSIT, 5.0, spam=True),
            rec(DEPOSIT, DEPOSIT, 10.0), rec("0x" + "88" * 20, DEPOSIT, None, asset="ETH"),
            rec(STRANGER, "0x" + "99" * 20, 1_000.0)]
    valued, unvalued = ds.senders(rows, DEPOSIT, CLUSTER)
    assert set(valued) == {STRANGER}
    assert round(valued[STRANGER]["usd"], 2) == 249_993.84
    assert unvalued == {"0x" + "88" * 20}


def test_a_test_payment_below_dust_does_not_become_a_known_sender():
    """A $10 test first, the real money later: the later payment must still be news."""
    valued, _ = ds.senders([rec(STRANGER, DEPOSIT, 10.0)], DEPOSIT, CLUSTER)
    assert valued == {}


# --- what is news ------------------------------------------------------------------------

def _rows(cls):
    found = {DEPOSIT: ds.senders([rec(STRANGER, DEPOSIT, 249_993.84)], DEPOSIT, CLUSTER)[0]}
    return found, ds.sharers(found, {STRANGER: cls})


def test_a_sentinels_first_reading_is_a_baseline():
    """2024-07-31 is history that made the address worth watching, not breaking news."""
    _, rows = _rows(ds.CLASS_QUIET)
    assert ds.news(None, rows) == []
    assert ds.news({"known_senders": {}}, rows) == []


def test_a_new_quiet_sender_is_critical_and_an_unmeasured_one_is_high():
    previous = {"known_senders": {DEPOSIT: []}}
    _, quiet = _rows(ds.CLASS_QUIET)
    _, unmeasured = _rows(ds.CLASS_UNMEASURED)
    assert [ds.severity(r) for r in ds.news(previous, quiet)] == ["CRITICAL"]
    assert [ds.severity(r) for r in ds.news(previous, unmeasured)] == ["HIGH"]


def test_a_busy_or_contract_sender_is_recorded_but_never_news():
    previous = {"known_senders": {DEPOSIT: []}}
    for cls in (ds.CLASS_BUSY, ds.CLASS_CONTRACT):
        _, rows = _rows(cls)
        assert rows and ds.news(previous, rows) == []


def test_a_known_sender_is_not_news_again():
    _, rows = _rows(ds.CLASS_QUIET)
    assert ds.news({"known_senders": {DEPOSIT: [STRANGER]}}, rows) == []


def test_an_undelivered_alert_is_kept_out_of_the_seen_set_for_retry():
    found, rows = _rows(ds.CLASS_QUIET)
    known = ds.known_senders({"known_senders": {DEPOSIT: []}}, found, pending_alerts=rows)
    assert STRANGER not in known[DEPOSIT]
    known = ds.known_senders({"known_senders": {DEPOSIT: []}}, found, pending_alerts=[])
    assert STRANGER in known[DEPOSIT]


def test_the_severity_domain_is_routable():
    from src.alerts import ESCALATING_SEVERITIES
    for cls in (ds.CLASS_QUIET, ds.CLASS_UNMEASURED, ds.CLASS_BUSY, ds.CLASS_CONTRACT):
        assert ds.severity({"class": cls}) in ESCALATING_SEVERITIES


# --- the roster reads it from this detector's own file -----------------------------------

def test_a_quiet_sharer_carries_linkage_in_the_roster(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "DATA_DIR", tmp_path)
    (tmp_path.parent / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path.parent / "profile" / "backtest.json").write_text(json.dumps({"passed": False}))
    (tmp_path / "deposit_sentinels").mkdir(parents=True)
    (tmp_path / "deposit_sentinels" / "latest.json").write_text(json.dumps({"sharers": [
        {"sentinel": DEPOSIT, "address": STRANGER, "class": "quiet", "usd": 249_993.84,
         "first_ts": 1722428300},
        {"sentinel": DEPOSIT, "address": HOT, "class": "busy", "usd": 1_000_000.0},
    ]}))
    rows = {r["wallet"]: r for r in roster.build_roster({"target_wallet": T})["wallets"]}
    assert rows[STRANGER]["vectors"] == ["linkage"]
    assert rows[STRANGER]["evidence"]["shared_private_deposit_address"]["sentinel"] == DEPOSIT
    assert rows[STRANGER]["tier"] == roster.TIER_POSSIBLE
    assert HOT not in rows or "linkage" not in rows[HOT]["vectors"]


# --- the script -----------------------------------------------------------------------------

def test_readings_prefer_what_is_stored_and_bound_live_calls(tmp_path):
    import scripts.check_deposit_sentinels as script

    calls = []

    def fetch(address, chain):
        calls.append((address, chain))
        return QUIET

    r = script.Readings({STRANGER: {"arbitrum": QUIET}}, fetch, max_live=1, seconds=60)
    assert r.for_address(STRANGER, {"arbitrum"}) == [QUIET]
    assert calls == [], "a stored reading was taken again"
    r.for_address("0x" + "aa" * 20, {"arbitrum", "ethereum"})
    assert len(calls) == 1, "the live-reading bound was not honoured"


def test_hl_state_never_reports_a_failed_read_as_an_empty_account():
    import scripts.check_deposit_sentinels as script

    def boom(body):
        raise TimeoutError("read timed out")

    state = script.hl_state(STRANGER, post=boom)
    assert state["read_ok"] is False and "TimeoutError" in state["error"]


def test_main_baselines_then_alerts_a_new_sender(tmp_path, monkeypatch):
    """End to end over one sentinel: run one records the 2024 sender silently,
    run two sees a new quiet sender and alerts once."""
    import scripts.check_deposit_sentinels as script
    import scripts.check_watchlist as watch
    import src.alerts as alerts
    import src.chain.collect as collect
    from src import utils

    (utils.DATA_DIR / "transfer_graph").mkdir(parents=True)
    (utils.DATA_DIR / "transfer_graph" / "latest.json").write_text(json.dumps({"nodes": [
        {"wallet": DEPOSIT, "evidence": {"service_reason": "conduit: forwards 100%"}}]}))
    new_wallet = "0x" + "5e" * 20
    substrate = {"rows": [rec(T, DEPOSIT, 58_000_000.0), rec(STRANGER, DEPOSIT, 249_993.84)]}

    def by_wallet(wallets, **kw):
        return {w: [r for r in substrate["rows"] if w in (r["src"], r["dst"])] for w in wallets}

    sent = []
    monkeypatch.setattr(collect, "records_by_wallet", by_wallet)
    monkeypatch.setattr(watch, "sweep", lambda address, config, plan_refused=None: None)
    monkeypatch.setattr(alerts, "alert_deposit_address_shared",
                        lambda row, state=None: sent.append(row) or True)
    monkeypatch.setattr(script, "hl_state", lambda address, post=None: {"read_ok": True})
    monkeypatch.setattr(script, "load_config",
                        lambda: {"target_wallet": T, "known_self_wallets": [TREASURY]})
    fetched = {DEPOSIT: QUIET, STRANGER: QUIET, new_wallet: QUIET}
    monkeypatch.setattr("src.chain.activity.fetch_activity",
                        lambda address, chain, **kw: fetched.get(address))

    script.main()
    first = json.loads((utils.DATA_DIR / "deposit_sentinels" / "latest.json").read_text())
    assert DEPOSIT in first["sentinels"]
    assert [s["address"] for s in first["sharers"]] == [STRANGER]
    assert sent == [], "a baseline alerted"

    substrate["rows"].append(rec(new_wallet, DEPOSIT, 500_000.0, ts=1789600000))
    script.main()
    assert [r["address"] for r in sent] == [new_wallet]
    assert sent[0]["class"] == ds.CLASS_QUIET

    script.main()
    assert len(sent) == 1, "the same sender alerted twice"


def test_the_alert_renders_and_routes(monkeypatch):
    import src.alerts as alerts

    sent = []
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: sent.append((key, subject, body)) or True)
    row = {"sentinel": DEPOSIT, "address": STRANGER, "class": ds.CLASS_QUIET,
           "usd": 249_993.84, "count": 1, "last_ts": 1722428300,
           "chains": ["arbitrum"], "assets": ["USDC"]}
    assert alerts.alert_deposit_address_shared(row, {"read_ok": True, "role": "user",
                                                     "account_value": "6.0",
                                                     "birth": "2024-07-31", "fills": 0})
    key, subject, body = sent[0]
    assert alerts._severity_of(subject) == "CRITICAL"
    assert STRANGER in body and DEPOSIT in body and "2024-07-31" in body
    assert alerts.alert_deposit_address_shared({**row, "class": ds.CLASS_UNMEASURED}, None)
    assert alerts._severity_of(sent[1][1]) == "HIGH"
    assert "could not be read" in sent[1][2]


def test_the_reading_budget_starts_at_the_first_live_reading():
    """The watch builds its readings before the wallet loop and reaches the
    payee check a minute later. A deadline set at construction had always
    expired, so no live reading was ever taken (2026-09-16)."""
    import scripts.check_deposit_sentinels as script

    now = [0.0]
    r = script.Readings({}, lambda address, chain: QUIET, max_live=5, seconds=45,
                        clock=lambda: now[0])
    now[0] = 90.0                      # the watch loop ran for a minute and a half
    assert r.for_address(STRANGER, {"arbitrum"}) == [QUIET], "budget spent before it began"
    now[0] = 200.0                     # and the bound still holds once it has started
    assert r.for_address("0x" + "ab" * 20, {"arbitrum"}) == []
