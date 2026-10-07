"""scripts/run_study.py end to end, network-free."""

import json
import re
from pathlib import Path

import pytest

from scripts import run_study
from src.study import archive, records

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40
NOW = 1_791_000_000_000  # 2026-10-03 04:00:00 UTC
CONFIG = {"target_wallet": T, "known_self_wallets": [], "watch_wallets": [],
          "study_wallets": []}


def program(start, n, coin="BTC", step=1_700):
    return [{"coin": coin, "side": "A", "sz": "0.1", "px": "100.0", "time": start + i * step,
             "crossed": True, "oid": start + i * step, "tid": start + i * step}
            for i in range(n)]


def entries(fills):
    return [{"oid": f["oid"], "order": {"coin": f["coin"], "side": f["side"], "limitPx": "95.0",
                                        "oid": f["oid"], "timestamp": f["time"], "tif": "Ioc",
                                        "cloid": None, "isTrigger": False, "orderType": "Limit",
                                        "reduceOnly": False}, "status": "filled"}
            for f in fills]


class Fake:
    """Hyperliquid's info API for a few wallets: only the newest 10,000 fills exist."""

    def __init__(self, fills=None, orders=None, errors=None):
        self.fills, self.orders, self.errors = fills or {}, orders or {}, errors or {}
        self.calls = []

    def __call__(self, body):
        kind, user = body["type"], body.get("user")
        self.calls.append((kind, user))
        if (kind, user) in self.errors:
            return {"ok": False, "error": self.errors[(kind, user)]}
        if kind == "userFillsByTime":
            kept = sorted(self.fills.get(user, []), key=lambda f: f["time"])[-10_000:]
            rows = [f for f in kept if body["startTime"] <= f["time"] <= body["endTime"]]
            return {"ok": True, "data": rows[:2_000]}
        if kind == "userFills":
            return {"ok": True, "data": self.fills.get(user, [])[-2_000:]}
        if kind == "historicalOrders":
            return {"ok": True, "data": self.orders.get(user, [])[-2_000:]}
        return {"ok": True, "data": []}


def lead(wallet, value=2e6):
    return {"wallet": wallet, "tier": "POSSIBLE", "is_service": False,
            "evidence": {"hl_role": "user", "hl_account_value": value}}


def data_dir(tmp_path, roster_rows, surface=None):
    data = tmp_path / "data"
    (data / "roster").mkdir(parents=True)
    (data / "roster" / "latest.json").write_text(json.dumps({"wallets": roster_rows}))
    his = program(NOW - 3 * records.DAY_MS, 300)
    (data / "fills").mkdir()
    (data / "fills" / "2026-09-30.json").write_text(json.dumps(his))
    (data / "orders").mkdir()
    (data / "orders" / "2026-09-30.json").write_text(json.dumps(entries(his)))
    if surface:
        (data / "hl_surface").mkdir()
        (data / "hl_surface" / "latest.json").write_text(json.dumps(surface))
    return data


def test_a_run_reads_folds_and_writes_every_output(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(T)])
    fills = program(NOW - 2 * records.DAY_MS, 250)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert [r["wallet"] for r in doc["wallets"]] == [A] and doc["read"] == 1
    assert all(user != T for _kind, user in fake.calls)
    study = data / "study"
    assert (study / "latest.json").exists() and (study / "wallets" / f"{A}.json").exists()
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 250
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 250
    state = archive.load_state(data)
    assert state["wallets"][A]["fills_cursor_ms"] > NOW - 14 * records.DAY_MS
    assert doc["wallets"][0]["families"]["tooling"]["verdict"] == "uncalibrated"


def test_the_target_is_never_studied_even_when_pinned(tmp_path):
    data = data_dir(tmp_path, [lead(T)])
    fake = Fake()
    config = {**CONFIG, "watch_wallets": [T], "study_wallets": [T]}
    doc = run_study.run(data_dir=data, config=config, now_ms=NOW, fetch=fake)
    assert doc["studied"] == 0 and fake.calls == []


def test_a_failed_read_leaves_the_cursor_and_writes_nothing(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fake = Fake(errors={("userFillsByTime", A): "HTTP 500"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert doc["unreadable"] == [{"wallet": A, "error": "HTTP 500"}] and doc["read"] == 0
    assert "fills_cursor_ms" not in archive.load_state(data)["wallets"][A]
    assert not archive.wallet_dir(A, data).exists()


def test_a_stop_after_the_fills_fold_keeps_the_fold_and_retries_the_orders(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)},
                errors={("historicalOrders", A): "time_budget"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    mstate = archive.load_state(data)["wallets"][A]
    assert doc["stopped"] and "fills_cursor_ms" in mstate and "orders_read_ms" not in mstate
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000,
                  fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 50
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 50


def test_a_busy_wallets_newest_orders_still_count(tmp_path):
    # Its newest 2,000 orders can all be minutes old; they must still be read
    # (the 2026-10-06 dry run found two bots whose habits were never recorded).
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 50 * 60_000, 2_500, step=1_000)
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW,
                  fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    assert day["habits"]["orders_seen"] == 2_000


def test_a_saturated_read_moves_the_cursor_without_claiming_the_gap(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 4 * records.HOUR_MS, 12_000, step=1_000)  # one a second from 00:00
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: fills}))
    first_read = fills[2_000]["time"]  # only the newest 10,000 exist
    day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    assert day["coverage"]["fills"][0][0] == first_read and day["coverage"]["saturated"]
    mstate = archive.load_state(data)["wallets"][A]
    assert mstate["fills_cursor_ms"] == NOW - records.HOUR_MS  # the 03:00 hour mark
    assert day["coverage"]["runs_split"]


def test_family_members_are_measured_and_kept(tmp_path):
    surface = {"subaccounts": {B: {"master": A}}}
    data = data_dir(tmp_path, [lead(A)], surface=surface)
    fills = program(NOW - 2 * records.DAY_MS, 150)  # 100+ orders: measurable members
    fake = Fake(fills={A: fills, B: fills}, orders={A: entries(fills), B: entries(fills)})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    panel = archive.read_json(data / "study" / "panel" / "families.json", {})
    assert set(panel["members"]) == {A, B} and panel["families"] == {A: [A, B]}
    assert doc["panels"]["family_pairs"] == 1


def test_the_study_step_is_bounded_inside_its_job():
    workflow = (Path(__file__).parent.parent / ".github/workflows/study.yml").read_text()
    job_text = workflow.split("\n  study:\n", 1)[1]  # the gate job has its own timeout
    job = int(re.search(r"\n    timeout-minutes:\s*(\d+)", job_text).group(1))
    step = job_text.split("name: Study the candidates", 1)[1].split("- name:", 1)[0]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step).group(1))
    assert run_study.READ_SECONDS + 60 < minutes * 60 < job * 60


# --- What the brief specifies and the tests above leave open --------------------------

class LedgerFake(Fake):
    """Fake, plus the ledger: `ledgers[wallet]` rows are served by time."""

    def __init__(self, *args, ledgers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.ledgers = ledgers or {}

    def __call__(self, body):
        if body["type"] != "userNonFundingLedgerUpdates":
            return super().__call__(body)
        self.calls.append((body["type"], body.get("user")))
        if (body["type"], body["user"]) in self.errors:
            return {"ok": False, "error": self.errors[(body["type"], body["user"])]}
        rows = [r for r in self.ledgers.get(body["user"], [])
                if body["startTime"] <= r["time"] <= body["endTime"]]
        return {"ok": True, "data": rows[:2_000]}


def reads(fake, kind):
    return [user for k, user in fake.calls if k == kind]


class Budget:
    """A ReadBudget stand-in that allows `n` more questions."""

    def __init__(self, n=10 ** 9):
        self.n = n

    def can_continue(self):
        self.n -= 1
        return self.n >= 0


def test_orders_and_ledger_are_read_once_a_day_and_the_ledger_is_folded(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    deposit = {"time": NOW - records.DAY_MS, "hash": "0x" + "1" * 64,
               "delta": {"type": "deposit", "usdc": "250000.0"}}

    def fake():
        return LedgerFake(fills={A: fills}, orders={A: entries(fills)}, ledgers={A: [deposit]})

    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake())
    state = archive.load_state(data)["wallets"][A]
    assert state["orders_cursor_ms"] == state["ledger_cursor_ms"] == state["orders_read_ms"] == NOW
    day = archive.load_days(A, "2026-10-02", "2026-10-02", data)["2026-10-02"]
    assert [(r["type"], r["usd"]) for r in day["ledger"]] == [("deposit", 250_000.0)]
    hourly = fake()  # an hour later: the new fills again, the orders and the ledger not yet
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + records.HOUR_MS, fetch=hourly)
    assert reads(hourly, "userFillsByTime") == [A]
    assert reads(hourly, "historicalOrders") == []
    assert reads(hourly, "userNonFundingLedgerUpdates") == []
    daily = fake()
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 25 * records.HOUR_MS, fetch=daily)
    assert reads(daily, "historicalOrders") == [A]
    assert reads(daily, "userNonFundingLedgerUpdates") == [A]


def test_a_full_orders_page_vouches_only_from_its_oldest_order(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(B)])
    busy = program(NOW - 50 * 60_000, 2_500, step=1_000)  # the page holds the newest 2,000
    quiet = program(NOW - 2 * records.DAY_MS, 50)  # the page holds all of them
    fake = Fake(fills={A: busy, B: quiet}, orders={A: entries(busy), B: entries(quiet)})
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    full = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    assert full["coverage"]["orders"] == [[busy[500]["time"], NOW]]
    first = records.day_of(NOW - 14 * records.DAY_MS)
    whole = archive.load_days(B, first, first, data)[first]
    assert whole["coverage"]["orders"] == [
        [NOW - 14 * records.DAY_MS, records.day_start_ms(first) + records.DAY_MS]]


def earlier_run(data, wallet=A, *, cursor, last_fill):
    """What an earlier run left in state.json for a wallet."""
    archive.save_state({"wallets": {wallet: {"fills_cursor_ms": cursor, "last_fill_ms": last_fill}}},
                       data)


def test_a_saturated_read_with_nothing_old_enough_moves_the_cursor_and_folds_nothing(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    earlier_run(data, cursor=NOW - 6 * records.HOUR_MS, last_fill=NOW - 7 * records.HOUR_MS)
    fills = program(NOW - 130_000, 12_000, step=10)  # 100 a second, all inside five minutes
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: fills}))
    state = archive.load_state(data)["wallets"][A]
    # the last fill of the earlier run is in the span the API no longer serves: forgotten
    assert state["fills_cursor_ms"] == fills[2_000]["time"] and state["last_fill_ms"] is None
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert all(d["coverage"]["fills"] == [] and d["fills"] == 0 for d in days.values())


def test_a_saturated_read_does_not_claim_a_session_start_it_cannot_see(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    earlier_run(data, cursor=NOW - 6 * records.HOUR_MS, last_fill=NOW - 7 * records.HOUR_MS)
    fills = program(NOW - 4 * records.HOUR_MS, 12_000, step=1_000)  # a fill a second
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: fills}))
    day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
    # the first fill read (00:33:20) follows hours of unread time, so whether it opened a
    # session is unknown, and no later fill is 30 minutes after the one before it
    assert [d for d in day["decisions"] if d[3] == "session"] == []


def test_a_spent_budget_ends_the_run_before_any_read_and_the_rows_still_come(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(B)])
    fake = Fake()
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake, read_seconds=0)
    assert doc["stopped"] and doc["read"] == 0 and doc["unreadable"] == [] and fake.calls == []
    assert doc["budget"]["stopped_reason"] == "time_budget"
    assert [r["wallet"] for r in doc["wallets"]] == [A, B]


def test_a_stop_ends_the_family_reads_too_and_the_wallet_it_left_goes_first(tmp_path):
    surface = {"subaccounts": {B: {"master": A}}}
    data = data_dir(tmp_path, [lead(A), lead(B)], surface=surface)
    fills = program(NOW - 2 * records.DAY_MS, 50)
    served = {"fills": {A: fills, B: fills}, "orders": {A: entries(fills), B: entries(fills)}}
    stopped = Fake(**served, errors={("userFillsByTime", B): "rate_limited"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=stopped)
    assert doc["stopped"] and doc["read"] == 1
    assert reads(stopped, "userFillsByTime") == [A, B] and reads(stopped, "userFills") == []
    resumed = Fake(**served)
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000, fetch=resumed)
    assert reads(resumed, "userFillsByTime") == [B, A]


def test_measure_families_takes_a_few_stale_members_and_never_records_a_failed_read():
    fills = program(NOW - 2 * records.DAY_MS, 150)
    w = ["0x" + c * 40 for c in "123456"]
    served = {"fills": {x: fills for x in w}, "orders": {x: entries(fills) for x in w}}
    fams = {w[0]: w[:3], w[3]: w[3:]}
    fresh, stale = {"at_ms": NOW - 29 * records.DAY_MS}, {"at_ms": NOW - 31 * records.DAY_MS}
    panel = {"members": {w[1]: dict(fresh), w[4]: dict(stale)}}
    assert run_study.measure_families(fams, panel, NOW, Budget(), Fake(**served), limit=3) == 3
    assert {x: panel["members"][x]["at_ms"] for x in panel["members"]} == {
        w[0]: NOW, w[1]: fresh["at_ms"], w[2]: NOW, w[3]: NOW, w[4]: stale["at_ms"]}
    failing = Fake(**served, errors={("userFills", w[0]): "HTTP 500"})
    panel = {}
    assert run_study.measure_families(fams, panel, NOW, Budget(), failing) == 5
    assert set(panel["members"]) == set(w[1:])  # the failed one is simply not recorded
    refused = Fake(**served, errors={("userFills", w[1]): "rate_limited"})
    panel = {}
    assert run_study.measure_families(fams, panel, NOW, Budget(), refused) == 1
    assert set(panel["members"]) == {w[0]}  # a stop ends the pass
    panel = {}
    assert run_study.measure_families(fams, panel, NOW, Budget(2), Fake(**served)) == 2


def test_his_days_are_built_by_the_candidates_code_and_marked_as_his(tmp_path):
    data = data_dir(tmp_path, [])
    withdrawal = {"time": NOW - 3 * records.DAY_MS + 60_000, "hash": "0x" + "2" * 64,
                  "delta": {"type": "withdraw", "usdc": "5000.0"}}
    (data / "ledger").mkdir()
    (data / "ledger" / "2026-09-30.json").write_text(json.dumps([withdrawal]))
    (data / "fills" / "2026-09-29.json").write_text(json.dumps([{"coin": "BTC"}, "x", {"time": "?"}]))
    (data / "orders" / "2026-09-29.json").write_text(json.dumps([{"oid": 1}, 5]))
    days = run_study.his_days(data, T)  # rows with no time, or no order, are not his
    assert {(d["role"], d["wallet"]) for d in days.values()} == {("target", T)}
    assert sum(d["orders"] for d in days.values()) == 300
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 300
    assert [(r["type"], r["usd"]) for d in days.values() for r in d["ledger"] or []] == [
        ("withdraw", 5_000.0)]
    assert run_study.his_days(tmp_path / "nowhere", T) == {}


def test_the_study_set_is_capped_by_the_configs_max_wallets(tmp_path):
    wallets = ["0x" + c * 40 for c in "123"]
    data = data_dir(tmp_path, [lead(w) for w in wallets])
    fake = Fake()
    doc = run_study.run(data_dir=data, config={**CONFIG, "study": {"max_wallets": 2}},
                        now_ms=NOW, fetch=fake)
    assert doc["studied"] == 2 and doc["read"] == 2 and len(set(reads(fake, "userFillsByTime"))) == 2


def test_the_target_and_his_cluster_never_enter_a_panel(tmp_path):
    other = "0x" + "c" * 40
    data = data_dir(tmp_path, [], surface={"subaccounts": {B: {"master": A}}})
    habits = {T: {"orders_seen": 500}, A: {"orders_seen": 500}, other: {"orders_seen": 500}}
    (data / "execution_program").mkdir()
    (data / "execution_program" / "census_state.json").write_text(json.dumps({"habits": habits}))
    fake = Fake()
    config = {**CONFIG, "target_wallet": "0x" + T[2:].upper(),  # however the config spells them
              "known_self_wallets": ["0x" + A[2:].upper()]}
    doc = run_study.run(data_dir=data, config=config, now_ms=NOW, fetch=fake)
    assert doc["target"] == T
    assert doc["panels"]["measurable_strangers"] == 1  # `other` only
    assert archive.read_json(data / "study" / "panel" / "families.json", {})["families"] == {}
    assert fake.calls == []


def test_sealed_months_are_rolled_and_an_unchanged_dossier_is_not_rewritten(tmp_path, monkeypatch):
    data = data_dir(tmp_path, [lead(A)])
    archive.save_days({"2026-09-20": records.empty_day(A, "2026-09-20", "studied")}, data)
    fills = program(NOW - 2 * records.DAY_MS, 50)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)})
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    folder = archive.wallet_dir(A, data)
    assert (folder / "2026-09.jsonl.gz").exists() and not (folder / "2026-09-20.json").exists()
    assert archive.load_days(A, "2026-09-20", "2026-09-20", data)["2026-09-20"]["wallet"] == A
    written, real = [], archive.write_compact
    monkeypatch.setattr(archive, "write_compact",
                        lambda path, doc: (written.append(Path(path).name), real(path, doc))[1])
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000, fetch=fake)
    assert f"{A}.json" not in written


def test_the_study_clocks_are_kept_between_runs(tmp_path):
    decayed = lead(A)
    decayed.update(tier="WATCH", tier_dropped_from="POSSIBLE")
    data = data_dir(tmp_path, [decayed])
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake())
    state = archive.load_state(data)
    assert state["decayed_seen"] == {A: NOW}
    assert state["members"] == {A: {"source": "decayed_lead", "since_ms": NOW}}
    month = NOW + 30 * records.DAY_MS
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=month, fetch=Fake())
    assert doc["wallets"][0]["studied_since_ms"] == NOW
    assert archive.load_state(data)["members"][A]["since_ms"] == NOW
    over = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 61 * records.DAY_MS,
                         fetch=Fake())
    assert over["studied"] == 0  # 60 days from first seen decayed


def test_the_command_line_caps_the_set_and_the_reads(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {"studied": 3, "read": 2, "stopped": False, "panels": {},
                "unreadable": [{"wallet": A, "error": "x"}],
                "partial": [{"wallet": B, "source": "orders", "error": "e"},
                            {"wallet": B, "source": "ledger", "error": "e"}]}

    monkeypatch.setattr(run_study, "run", fake_run)
    assert run_study.main(["--data-dir", str(tmp_path), "--max-wallets", "5",
                           "--read-seconds", "60"]) == 0
    assert seen["data_dir"] == tmp_path and seen["read_seconds"] == 60
    assert seen["config"]["study"]["max_wallets"] == 5
    # wallets, not entries: B is partial once although both its sources failed
    assert "[study] 3 studied, 2 read, 1 unreadable, 1 partial, stopped=False" in (
        capsys.readouterr().out)


def test_a_failed_orders_or_ledger_read_is_retried_and_what_was_folded_stays(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(B)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    served = {"fills": {A: fills, B: fills}, "orders": {A: entries(fills), B: entries(fills)}}
    broken = Fake(**served, errors={("historicalOrders", A): "HTTP 500",
                                    ("userNonFundingLedgerUpdates", B): "HTTP 500"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=broken)
    state = archive.load_state(data)["wallets"]
    assert not doc["stopped"] and doc["read"] == 2  # a failure is not a stop
    assert "fills_cursor_ms" in state[A] and "orders_cursor_ms" not in state[A]
    assert state[A]["ledger_cursor_ms"] == NOW  # A's orders failed, its ledger was still read
    assert "orders_cursor_ms" in state[B] and "ledger_cursor_ms" not in state[B]
    assert "orders_read_ms" not in state[A] and "orders_read_ms" not in state[B]
    assert reads(broken, "userNonFundingLedgerUpdates") == [A, B]
    assert {(p["wallet"], p["source"]) for p in doc["partial"]} == {(A, "orders"), (B, "ledger")}
    healthy = Fake(**served)
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000, fetch=healthy)
    assert reads(healthy, "historicalOrders") == [A, B]  # both retried at once
    for wallet in (A, B):
        days = archive.load_days(wallet, "2026-09-01", "2026-10-31", data)
        assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 50


DEPOSIT = {"time": NOW - records.DAY_MS, "hash": "0x" + "1" * 64,
           "delta": {"type": "deposit", "usdc": "250000.0"}}


def test_the_last_fill_carries_between_runs_so_a_session_is_decided_once(tmp_path):
    a1, a2 = NOW - 100 * records.MINUTE_MS, NOW - 30 * records.MINUTE_MS
    spell1, spell2 = program(a1, 61, step=10_000), program(a2, 61, step=10_000)  # 10 minutes each
    stream = {A: spell1 + spell2}  # an hour of silence between them
    two = data_dir(tmp_path / "two", [lead(A)])
    # the first run reads while the second spell is on: its boundary is where that spell began
    run_study.run(data_dir=two, config=CONFIG, now_ms=a2 + 8 * records.MINUTE_MS,
                  fetch=Fake(fills=stream))
    assert archive.load_state(two)["wallets"][A]["last_fill_ms"] == spell1[-1]["time"]
    run_study.run(data_dir=two, config=CONFIG, now_ms=NOW, fetch=Fake(fills=stream))
    one = data_dir(tmp_path / "one", [lead(A)])
    run_study.run(data_dir=one, config=CONFIG, now_ms=NOW, fetch=Fake(fills=stream))

    def decisions(data):
        return archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]["decisions"]

    assert [d for d in decisions(one) if d[3] == "session"] == [
        [a1, "BTC", "A", "session"], [a2, "BTC", "A", "session"]]
    assert decisions(two) == decisions(one)  # none decided twice, none missed
    assert archive.load_state(two)["wallets"][A]["last_fill_ms"] == spell2[-1]["time"]


def test_a_ledger_longer_than_three_pages_moves_its_cursor_only_to_what_was_read(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    rows = [{"time": NOW - 3 * records.DAY_MS + i * 1_000, "hash": f"0x{i:064x}",
             "delta": {"type": "deposit", "usdc": "10.0"}} for i in range(6_001)]
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=LedgerFake(ledgers={A: rows}))
    read_to = rows[5_997]["time"]  # three pages of 2,000, each starting on the last row before
    assert archive.load_state(data)["wallets"][A]["ledger_cursor_ms"] == read_to != NOW
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert max(hi for d in days.values() for _lo, hi in d["coverage"]["ledger"]) == read_to


def test_the_standard_programs_ioc_offsets_are_measured_from_the_fills_read_with_them(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 150)  # limit 95 on a first fill at 100: 5.0%
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW,
                  fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    day = archive.load_days(A, "2026-10-01", "2026-10-01", data)["2026-10-01"]
    habits = day["habits"]
    assert habits["ioc_offset_5pct"] == habits["ioc_offset_seen"] > 0


def test_a_failed_orders_read_is_partial_and_does_not_block_the_ledger(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    served = {"fills": {A: fills}, "orders": {A: entries(fills)}, "ledgers": {A: [DEPOSIT]}}
    broken = LedgerFake(**served, errors={("historicalOrders", A): "HTTP 500"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=broken)
    assert doc["partial"] == [{"wallet": A, "source": "orders", "error": "HTTP 500"}]
    assert doc["read"] == 1 and doc["unreadable"] == [] and not doc["stopped"]
    state = archive.load_state(data)["wallets"][A]
    assert state["ledger_cursor_ms"] == NOW and state["last_read_ms"] == NOW
    assert "orders_cursor_ms" not in state and "orders_read_ms" not in state
    day = archive.load_days(A, "2026-10-02", "2026-10-02", data)["2026-10-02"]
    assert [(r["type"], r["usd"]) for r in day["ledger"]] == [("deposit", 250_000.0)]
    healthy = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000,
                            fetch=LedgerFake(**served))  # the failed source is retried
    assert healthy["partial"] == []
    assert archive.load_state(data)["wallets"][A]["orders_read_ms"] == NOW + 60_000
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 50


def test_a_failed_ledger_read_is_partial_and_the_orders_stay_folded(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    broken = LedgerFake(fills={A: fills}, orders={A: entries(fills)}, ledgers={A: [DEPOSIT]},
                        errors={("userNonFundingLedgerUpdates", A): "HTTP 500"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=broken)
    assert doc["partial"] == [{"wallet": A, "source": "ledger", "error": "HTTP 500"}]
    assert doc["read"] == 1 and doc["unreadable"] == [] and not doc["stopped"]
    state = archive.load_state(data)["wallets"][A]
    assert state["orders_cursor_ms"] == NOW and state["last_read_ms"] == NOW
    assert "ledger_cursor_ms" not in state and "orders_read_ms" not in state
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 50


def test_both_reads_failing_are_both_listed_in_the_order_they_were_tried(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    broken = LedgerFake(errors={("historicalOrders", A): "HTTP 500",
                                ("userNonFundingLedgerUpdates", A): "Timeout"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=broken)
    assert doc["partial"] == [{"wallet": A, "source": "orders", "error": "HTTP 500"},
                              {"wallet": A, "source": "ledger", "error": "Timeout"}]
    assert doc["read"] == 1 and "orders_read_ms" not in archive.load_state(data)["wallets"][A]


def test_a_stop_on_the_ledger_read_still_ends_the_wallet_and_is_not_partial(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    stopped = LedgerFake(fills={A: fills}, orders={A: entries(fills)}, ledgers={A: [DEPOSIT]},
                         errors={("userNonFundingLedgerUpdates", A): "rate_limited"})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=stopped)
    assert doc["stopped"] and doc["read"] == 0 and doc["partial"] == [] and doc["unreadable"] == []
    state = archive.load_state(data)["wallets"][A]
    assert "fills_cursor_ms" in state and state["orders_cursor_ms"] == NOW  # what was folded stays
    assert "ledger_cursor_ms" not in state and "orders_read_ms" not in state


def bursts(start, n, burst_s=300, silence_s=40, step_s=2):
    """A bot that works in `burst_s` bursts a `silence_s` apart: no quiet gap of 30 s inside a
    burst, one between bursts, and never 30 minutes of silence, so it is one session."""
    out, t = [], start
    for _ in range(n):
        out += program(t, burst_s // step_s, step=step_s * 1_000)
        t += (burst_s + silence_s) * 1_000
    return out


def dies_once(monkeypatch):
    """Make the first his_days of the run raise, as a crash or a step timeout after the
    wallets were read would; later calls work."""
    real, state = run_study.his_days, {"dead": False}

    def his_days(*args, **kwargs):
        if not state["dead"]:
            state["dead"] = True
            raise RuntimeError("the run died after reading its wallets")
        return real(*args, **kwargs)

    monkeypatch.setattr(run_study, "his_days", his_days)


@pytest.mark.parametrize("errors", [{}, {("historicalOrders", A): "time_budget"}],
                         ids=["read", "stopped"])
def test_state_is_saved_with_each_wallets_days_not_only_at_the_end(tmp_path, monkeypatch, errors):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    dies_once(monkeypatch)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)}, errors=errors)
    with pytest.raises(RuntimeError, match="the run died"):
        run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    saved = archive.load_state(data)
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    covered_to = max(hi for d in days.values() for _lo, hi in d["coverage"]["fills"])
    assert saved["wallets"][A]["fills_cursor_ms"] == covered_to == NOW - 5 * records.MINUTE_MS
    assert saved["wallets"][A]["last_fill_ms"] == fills[-1]["time"] and A in saved["members"]


def test_a_run_that_died_leaves_no_spurious_session_for_the_recovery_run(tmp_path, monkeypatch):
    start = NOW - records.HOUR_MS
    stream = bursts(start, 8)  # eight bursts of five minutes: one session, opened at `start`
    died_at = NOW - 35 * records.MINUTE_MS  # mid-burst: the run folds up to the burst's start

    def sessions(data):
        day = archive.load_days(A, "2026-10-03", "2026-10-03", data)["2026-10-03"]
        return [d for d in day["decisions"] if d[3] == "session"]

    one = data_dir(tmp_path / "one", [lead(A)])
    run_study.run(data_dir=one, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: stream}))
    dies_once(monkeypatch)
    two = data_dir(tmp_path / "two", [lead(A)])
    with pytest.raises(RuntimeError, match="the run died"):
        run_study.run(data_dir=two, config=CONFIG, now_ms=died_at, fetch=Fake(fills={A: stream}))
    run_study.run(data_dir=two, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: stream}))
    assert sessions(one) == [[start, "BTC", "A", "session"]]  # what one read decides
    assert sessions(two) == sessions(one)  # and what a read, a death and a recovery decide


def test_a_foreign_file_of_the_wrong_shape_is_read_as_absent(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    for name, body in (("dormancy/latest.json", "[1, 2]"), ("newborn/latest.json", "{"),
                       ("execution_program/census.json", '{"hits": [null, "x", {"wallet": 7}]}'),
                       ("tape/latest.json", "[1, 2]"),
                       ("execution_program/census_state.json", "{"),
                       ("provenance/latest.json", "null"), ("hl_surface/latest.json", "[1, 2]")):
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        (data / name).write_text(body)
    assert run_study.detector_wallets(data) == []
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake())
    assert doc["studied"] == 1 and doc["unreadable"] == []


def test_a_first_read_reaches_back_fourteen_days(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    inside, outside = program(NOW - 13 * records.DAY_MS, 20), program(NOW - 15 * records.DAY_MS, 20)
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake(fills={A: outside + inside}))
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 20


def test_the_rows_read_the_last_120_days_counting_today(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(B)])
    inside = records.day_of(NOW - 119 * records.DAY_MS)
    corrupt_day(data, A, inside)
    corrupt_day(data, B, records.day_of(NOW - 120 * records.DAY_MS))
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=Fake())
    assert [u["wallet"] for u in doc["unreadable"]] == [A]


def test_a_wallet_that_leaves_the_set_keeps_its_archive_and_its_dossier_says_since_when(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)})
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    before = archive.load_state(data)  # A still a member
    (data / "roster" / "latest.json").write_text(json.dumps({"wallets": []}))
    later = NOW + 15 * records.DAY_MS  # past the 14 days a new member keeps its place
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=later, fetch=fake)
    dossier_path = data / "study" / "wallets" / f"{A}.json"
    assert doc["studied"] == 0 and archive.read_json(dossier_path, {})["left_ms"] == later
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 50
    # a run that died after dating the dossier and before saving its state meets the
    # wallet leaving again: the date it was first given stands
    archive.save_state(before, data)
    run_study.run(data_dir=data, config=CONFIG, now_ms=later + records.HOUR_MS, fetch=fake)
    assert archive.read_json(dossier_path, {})["left_ms"] == later


# --- Change A: the order of detector finds -------------------------------------------

def write_detector_files(data, files):
    for name, doc in files.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))


def test_detector_finds_come_strongest_first(tmp_path):
    census, tape, provenance, dormancy, newborn = ("0x" + c * 40 for c in "12345")
    data = tmp_path / "data"
    write_detector_files(data, {
        "dormancy/latest.json": {"handoffs": {dormancy: {"score": 0.5}}},
        "newborn/latest.json": {"newborn": [{"wallet": newborn, "account_value": 5e6}]},
        "execution_program/census.json": {"hits": [{"wallet": census}]},
        "tape/latest.json": {"program_hits": [{"wallet": tape}]},
        "provenance/latest.json": {"findings": [{"account": provenance}]},
    })
    assert run_study.detector_wallets(data) == [census, tape, provenance, dormancy, newborn]


def test_finds_within_a_source_go_by_score_and_by_value_above_a_million(tmp_path):
    low, high, big, bigger, small = ("0x" + c * 40 for c in "abcde")
    data = tmp_path / "data"
    write_detector_files(data, {
        "dormancy/latest.json": {"handoffs": {low: {"score": 0.4}, high: {"score": 0.9}}},
        "newborn/latest.json": {"newborn": [{"wallet": small, "account_value": 999_999.0},
                                            {"wallet": big, "account_value": 2e6},
                                            {"wallet": bigger, "account_value": 8e6}]},
    })
    assert run_study.detector_wallets(data) == [high, low, bigger, big]


# --- Change B: the study row's account value -----------------------------------------

def test_a_study_rows_value_is_the_total_not_the_perp_margin(tmp_path):
    row = lead(A, value=0.0)
    row["evidence"]["hl_total_value"] = 9.3e6  # $9.3M in spot, $0 of perp margin
    data = data_dir(tmp_path, [row])
    fills = program(NOW - 2 * records.DAY_MS, 150)
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW,
                        fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    assert doc["wallets"][0]["account_value"] == 9.3e6


def test_the_account_value_falls_back_to_perp_margin_and_then_to_nothing():
    assert run_study.account_value({"evidence": {"hl_total_value": 9, "hl_account_value": 5.0}}) == 9
    assert run_study.account_value({"evidence": {"hl_account_value": 5.0}}) == 5.0
    assert run_study.account_value({"evidence": {"hl_total_value": True,
                                                 "hl_account_value": 5.0}}) == 5.0
    assert run_study.account_value({"evidence": {"hl_total_value": "9.3e6"}}) is None
    assert run_study.account_value({"evidence": {}}) is None
    assert run_study.account_value({}) is None


# --- Change C: an unreadable archive stops that wallet, not the run ------------------

def corrupt_day(data, wallet, day="2026-10-01"):
    path = archive.wallet_dir(wallet, data) / f"{day}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{")
    return path


def test_an_unreadable_archive_stops_that_wallet_and_not_the_run(tmp_path):
    data = data_dir(tmp_path, [lead(A), lead(B)])
    corrupt = corrupt_day(data, A)  # inside the 14 days a first read reaches back
    fills = program(NOW - 2 * records.DAY_MS, 150)
    fake = Fake(fills={A: fills, B: fills}, orders={A: entries(fills), B: entries(fills)})
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert [u["wallet"] for u in doc["unreadable"]] == [A]
    assert doc["unreadable"][0]["error"].startswith("archive: ")
    state = archive.load_state(data)["wallets"]
    assert "fills_cursor_ms" not in state[A] and corrupt.read_bytes() == b"{"
    row = next(r for r in doc["wallets"] if r["wallet"] == A)
    assert row["families"] == {"tooling": {"verdict": "unreadable", "lr": None, "by": [],
                                           "key": {}, "basis": None}}
    assert row["rank"] == 0.0 and row["coverage_days"] is None and row["orders"] is None
    assert not (data / "study" / "wallets" / f"{A}.json").exists()
    # the healthy wallet beside it was read and folded normally
    assert doc["read"] == 1 and state[B]["fills_cursor_ms"] > NOW - 14 * records.DAY_MS
    days = archive.load_days(B, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 150
    assert (data / "study" / "wallets" / f"{B}.json").exists()
    assert next(r for r in doc["wallets"] if r["wallet"] == B)["families"]["tooling"][
        "verdict"] != "unreadable"


def test_a_save_that_finds_the_archive_unreadable_leaves_the_cursors(tmp_path, monkeypatch):
    # The load passed and the file went bad before the save: every cursor moved on a
    # copy, so none of it may reach the wallet's state.
    data = data_dir(tmp_path, [lead(A)])
    fills = program(NOW - 2 * records.DAY_MS, 50)

    def refuse(days, data_dir=None):
        raise archive.Unreadable("a day file went bad between the read and the save")

    monkeypatch.setattr(archive, "save_days", refuse)
    mstate = {"fills_cursor_ms": NOW - 3 * records.DAY_MS, "last_fill_ms": 7}
    result = run_study.study_wallet(A, mstate, NOW, data,
                                    Fake(fills={A: fills}, orders={A: entries(fills)}))
    assert result["status"] == "unreadable" and result["error"].startswith("archive: ")
    assert mstate == {"fills_cursor_ms": NOW - 3 * records.DAY_MS, "last_fill_ms": 7}
    assert not archive.wallet_dir(A, data).exists()


def test_a_bad_file_only_the_assembly_window_reaches_is_reported_there(tmp_path):
    # A first read reaches back 14 days and the assembly 120: day 2026-08-01 is read by
    # neither the fold nor the save, only by the rows.
    data = data_dir(tmp_path, [lead(A)])
    old = archive.wallet_dir(A, data) / "2026-08-01.json"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"{")
    fills = program(NOW - 2 * records.DAY_MS, 150)
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW,
                        fetch=Fake(fills={A: fills}, orders={A: entries(fills)}))
    assert doc["read"] == 1  # its new fills were read and folded
    assert [u["wallet"] for u in doc["unreadable"]] == [A]
    assert doc["unreadable"][0]["error"].startswith("archive: ")
    row = doc["wallets"][0]
    assert row["families"]["tooling"]["verdict"] == "unreadable"
    assert row["account_value"] == 2e6 and row["last_read_ms"] == NOW  # what is known stays known
    assert old.read_bytes() == b"{" and not (data / "study" / "wallets" / f"{A}.json").exists()


def test_a_repaired_archive_is_read_again_from_the_same_cursor(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    corrupt = corrupt_day(data, A)
    fills = program(NOW - 2 * records.DAY_MS, 150)
    fake = Fake(fills={A: fills}, orders={A: entries(fills)})
    run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    corrupt.unlink()
    doc = run_study.run(data_dir=data, config=CONFIG, now_ms=NOW + 60_000, fetch=fake)
    assert doc["unreadable"] == [] and doc["read"] == 1
    days = archive.load_days(A, "2026-09-01", "2026-10-31", data)
    assert sum(d["orders"] for d in days.values()) == 150
    assert sum((d["habits"] or {}).get("orders_seen", 0) for d in days.values()) == 150


def test_a_corrupt_state_fails_the_run_instead_of_starting_from_empty_cursors(tmp_path):
    data = data_dir(tmp_path, [lead(A)])
    (data / "study").mkdir()
    (data / "study" / "state.json").write_bytes(b"{")
    fake = Fake()
    with pytest.raises(archive.Unreadable):
        run_study.run(data_dir=data, config=CONFIG, now_ms=NOW, fetch=fake)
    assert fake.calls == [] and (data / "study" / "state.json").read_bytes() == b"{"


# --- Change D: one writer, pinned ----------------------------------------------------

STUDY_WRITERS = ("archive.save_days(", "archive.save_state(", "archive.roll_sealed_months(",
                 "archive.write_if_changed(", "archive.write_compact(",
                 "from src.study.archive import")
WRITERS_ALLOWED = ("src/study/archive.py", "scripts/run_study.py")


def test_only_the_run_script_writes_the_study():
    root = Path(__file__).parent.parent
    offenders = {}
    for folder in ("src", "scripts"):
        for path in sorted((root / folder).rglob("*.py")):
            name = path.relative_to(root).as_posix()
            if name in WRITERS_ALLOWED:
                continue
            text = path.read_text(encoding="utf-8")
            found = [writer for writer in STUDY_WRITERS if writer in text]
            if found:
                offenders[name] = found
    assert offenders == {}, "only scripts/run_study.py may write data/study/"
