"""The read-only CLI and its SQLite export (spec §13)."""

import json
import sqlite3

from scripts import casebook as cli
from scripts import update_casebook as update
from src.casebook import store

T = "0x45d26f28196d226497130c4bac709d808fed4029"
A, B = "0x" + "a" * 40, "0x" + "b" * 40


def build(tmp_path):
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps({
        "computed_at": "2026-10-08T00:00:00+00:00",
        "wallets": [{"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"],
                     "evidence": {"totals": {"received_from_target_usd": 1e6}}},
                    {"wallet": B, "tier": "POSSIBLE", "vectors": ["linkage"]}]}))
    update.run({"target_wallet": T, "known_self_wallets": []}, data_dir=tmp_path, out_dir=tmp_path,
               probe=False, alerts_on=False, now_ms=1_791_419_654_475)


def test_top_show_events_check_and_sqlite(tmp_path, capsys):
    build(tmp_path)
    args = ["--data-dir", str(tmp_path)]
    assert cli.main([*args, "top", "--all"]) == 0
    assert A in capsys.readouterr().out
    assert cli.main([*args, "show", A]) == 0
    shown = capsys.readouterr().out
    assert "direct_transfer" in shown and "hypurrscan.io/address/" + A in shown
    assert cli.main([*args, "events"]) == 0
    assert "case_opened" in capsys.readouterr().out
    assert cli.main([*args, "check"]) == 0
    db = tmp_path / "x.sqlite3"
    assert cli.main([*args, "sqlite", str(db)]) == 0
    con = sqlite3.connect(db)
    try:
        assert con.execute("select count(*) from cases").fetchone()[0] == 2
        assert con.execute("select kind from evidence where address=? and kind='direct_transfer'",
                           (A,)).fetchone()[0] == "direct_transfer"
        assert con.execute("select count(*) from events").fetchone()[0] > 0
    finally:
        con.close()


def test_top_without_an_index_says_so(tmp_path, capsys):
    assert cli.main(["--data-dir", str(tmp_path), "top"]) == 1
    assert "No casebook index" in capsys.readouterr().out


def test_check_fails_on_a_broken_case(tmp_path, capsys):
    build(tmp_path)
    store.case_path(A, tmp_path).write_text("{")
    assert cli.main(["--data-dir", str(tmp_path), "check"]) == 1
    assert "unreadable case" in capsys.readouterr().out


def test_show_an_unknown_address(tmp_path, capsys):
    build(tmp_path)
    assert cli.main(["--data-dir", str(tmp_path), "show", "0x" + "9" * 40]) == 1
    assert cli.main(["--data-dir", str(tmp_path), "show", "nonsense"]) == 1


def test_pct_formats_across_the_range():
    assert [cli.pct(p) for p in (0.999, 0.34, 0.034, 0.0034, 0.00001, None)] == \
           [">99%", "34%", "3.4%", "0.34%", "<0.1%", "-"]


def test_show_and_sqlite_say_what_no_longer_counts_and_why(tmp_path, capsys):
    funder = "0x" + "f9" * 20
    (tmp_path / "roster").mkdir(parents=True)
    (tmp_path / "roster" / "latest.json").write_text(json.dumps({
        "computed_at": "2026-10-08T00:00:00+00:00",
        "wallets": [{"wallet": B, "tier": "POSSIBLE", "vectors": ["linkage"],
                     "evidence": {"shared_first_funder": funder}}]}))
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "address_activity.json").write_text(json.dumps(
        {f"arbitrum:{funder}": {"txs": 2_282_986, "token_transfers": 1, "is_contract": False}}))
    update.run({"target_wallet": T, "known_self_wallets": []}, data_dir=tmp_path, out_dir=tmp_path,
               probe=False, alerts_on=False, now_ms=1_791_419_654_475)
    args = ["--data-dir", str(tmp_path)]
    assert cli.main([*args, "show", B]) == 0
    shown = capsys.readouterr().out
    assert "[invalidated] quiet_first_funder" in shown
    assert "no longer counts: global activity: 2,282,986 txs" in shown
    db = tmp_path / "x.sqlite3"
    assert cli.main([*args, "sqlite", str(db)]) == 0
    con = sqlite3.connect(db)
    try:
        status, why = con.execute("select status, invalid_reason from evidence where address=? "
                                  "and kind='quiet_first_funder'", (B,)).fetchone()
    finally:
        con.close()
    assert status == "invalidated" and why.startswith("global activity: 2,282,986 txs")
