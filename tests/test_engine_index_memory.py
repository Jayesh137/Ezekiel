"""The trace engine's substrate index is capped INSIDE the walk.

2026-11-14, run 43550464309: the engine sat 45 minutes inside an 8-minute step
until GitHub lost the runner, and the run's readings with it. Its index asked
`records_by_wallet` for every swept wallet (1,188) and trimmed busy ones
afterwards, so each full list existed first — the shape that took substrate
linkage to 15 GB on 2026-10-27. A starved runner cannot enforce a step timeout.
"""

import json

from scripts import run_trace_engine as rte
from src.chain import collect

HIM = "0xhim"


def _substrate(tmp_path, monkeypatch, records):
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    monkeypatch.setattr(collect, "_FILE_CACHE", type(collect._FILE_CACHE)())
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-11-01.json").write_text(json.dumps(records))


def _rec(i, src, dst, spam=False):
    return {"id": f"r{i}", "chain": "arbitrum", "src": src, "dst": dst,
            "amount_usd": 1.0, "ts": i, "spam": spam}


def test_a_quiet_wallet_gets_every_record_and_its_true_count(tmp_path, monkeypatch):
    _substrate(tmp_path, monkeypatch, [_rec(1, "0xquiet", "0xa"), _rec(2, "0xb", "0xquiet"),
                                       _rec(3, "0xother", "0xa")])
    rows, total = collect.records_by_wallet_capped(["0xQUIET"], cap=10)["0xquiet"]
    assert [r["id"] for r in rows] == ["r1", "r2"] and total == 2


def test_a_busy_wallet_never_holds_more_than_the_cap_plus_what_touches_him(tmp_path, monkeypatch):
    records = [_rec(i, "0xbusy", f"0xc{i}") for i in range(50)]
    records.insert(3, _rec(900, "0xbusy", HIM))          # before the cap is passed
    records.append(_rec(901, HIM, "0xbusy"))             # after it
    _substrate(tmp_path, monkeypatch, records)
    rows, total = collect.records_by_wallet_capped(["0xbusy"], cap=10, touching=[HIM])["0xbusy"]
    assert sorted(r["id"] for r in rows) == ["r900", "r901"]
    assert total == 52


def test_a_busy_wallets_kept_rows_stay_deduped_past_the_cap(tmp_path, monkeypatch):
    """The kept rows are the flows the engine sums as his money through the
    wallet: a record stored twice must not double one (rule 10)."""
    records = [_rec(900, "0xbusy", HIM)]                  # kept before the cap
    records += [_rec(i, "0xbusy", f"0xc{i}") for i in range(20)]
    records += [_rec(900, "0xbusy", HIM),                 # its duplicate, after
                _rec(901, HIM, "0xbusy"), _rec(901, HIM, "0xbusy")]
    _substrate(tmp_path, monkeypatch, records)
    rows, total = collect.records_by_wallet_capped(["0xbusy"], cap=10, touching=[HIM])["0xbusy"]
    assert sorted(r["id"] for r in rows) == ["r900", "r901"]
    assert total >= 22                                    # over-counting allowed, never under


def test_a_keep_all_wallet_is_never_trimmed(tmp_path, monkeypatch):
    _substrate(tmp_path, monkeypatch, [_rec(i, HIM, f"0xc{i}") for i in range(30)])
    rows, total = collect.records_by_wallet_capped([HIM], cap=5, keep_all=[HIM])[HIM]
    assert len(rows) == 30 and total == 30


def test_spam_is_left_out_and_duplicates_count_once_below_the_cap(tmp_path, monkeypatch):
    _substrate(tmp_path, monkeypatch, [_rec(1, "0xq", "0xa"), _rec(1, "0xq", "0xa"),
                                       _rec(2, "0xq", "0xpoison", spam=True)])
    rows, total = collect.records_by_wallet_capped(["0xq"], cap=10)["0xq"]
    assert len(rows) == 1 and total == 1


def test_every_requested_wallet_gets_an_answer_even_with_nothing_stored(tmp_path, monkeypatch):
    _substrate(tmp_path, monkeypatch, [])
    assert collect.records_by_wallet_capped(["0xnobody"], cap=5) == {"0xnobody": ([], 0)}
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "missing")
    assert collect.records_by_wallet_capped(["0xnobody"], cap=5) == {"0xnobody": ([], 0)}


def test_it_agrees_with_the_uncapped_reader_for_wallets_under_the_cap(tmp_path, monkeypatch):
    records = [_rec(i, f"0xw{i % 4}", f"0xw{(i + 1) % 4}") for i in range(40)]
    _substrate(tmp_path, monkeypatch, records)
    wallets = [f"0xw{i}" for i in range(4)]
    full = collect.records_by_wallet(wallets)
    capped = collect.records_by_wallet_capped(wallets, cap=1000)
    for w in wallets:
        assert [r["id"] for r in capped[w][0]] == [r["id"] for r in full[w]]
        assert capped[w][1] == len(full[w])


# --- the index: busyness is judged on the true size, memory on the cap ----------

def test_the_index_asks_for_capped_reads_and_keeps_the_true_total():
    asked = []

    def read(wallets, cap, keep_all=(), touching=()):
        asked.append((list(wallets), cap, set(keep_all), set(touching)))
        return {"0xbusy": ([{"src": "0xbusy", "dst": HIM}], 3_000_000),
                "0xquiet": ([{"src": "0xquiet", "dst": "0xa"}], 1)}

    index = rte.SubstrateIndex({HIM}, cap=500, read=read)
    got = index(["0xbusy", "0xquiet"])
    assert asked == [(["0xbusy", "0xquiet"], 500, {HIM}, {HIM})]
    assert getattr(got["0xbusy"], "total", None) == 3_000_000 and len(got["0xbusy"]) == 1
    assert len(got["0xquiet"]) == 1 and not hasattr(got["0xquiet"], "total")
    index(["0xbusy"])
    assert len(asked) == 1 and index.scans == 1                   # served from memory


def test_the_index_never_calls_the_reader_that_holds_every_record(monkeypatch, tmp_path):
    _substrate(tmp_path, monkeypatch, [_rec(i, "0xbusy", f"0xc{i}") for i in range(20)])

    def never(*a, **k):
        raise AssertionError("records_by_wallet holds every record of every wallet asked for")
    monkeypatch.setattr(collect, "records_by_wallet", never)
    index = rte.SubstrateIndex({HIM}, cap=5)
    rows = index(["0xbusy"])["0xbusy"]
    assert rows.total == 20 and list(rows) == []
