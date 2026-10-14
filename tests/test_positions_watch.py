"""Position notices: his book, read from the watch, diffed between runs, one message."""

from src import positions_watch as pw

W = "0x" + "a" * 40
W2 = "0x" + "b" * 40


def pos(coin, szi, value, entry="10", lev=None, mode="cross"):
    out = {"position": {"coin": coin, "szi": str(szi), "positionValue": str(value), "entryPx": entry}}
    if lev is not None:
        out["position"]["leverage"] = {"type": mode, "value": lev}
    return out


def test_book_keeps_signed_sizes_and_drops_dust_and_unreadable_rows():
    state = {"assetPositions": [pos("ZEC", -500, 20_000, lev=3), pos("xyz:SP500", 2, 13_000),
                                pos("DUST", 1, 5), {"position": {"coin": "X", "szi": "nan"}}, None]}
    book = pw.book(state)
    assert set(book) == {"ZEC", "xyz:SP500"}
    assert book["ZEC"] == {"size": -500.0, "side": "short", "entry": 10.0, "value": 20_000.0,
                           "leverage": 3.0, "mode": "cross"}
    assert pw.book(None) == {} and pw.book({}) == {}


def test_first_reading_is_a_baseline():
    assert pw.diff(None, {"ZEC": {"size": -1, "side": "short"}}) == []


def test_diff_reports_opened_closed_flipped_and_large_resizes_only():
    before = {"ZEC": {"size": -500.0, "side": "short", "value": 20_000.0},
              "BTC": {"size": 0.1, "side": "long", "value": 7_000.0},
              "ETH": {"size": 10.0, "side": "long", "value": 30_000.0},
              "SOL": {"size": -100.0, "side": "short", "value": 15_000.0}}
    after = {"ZEC": {"size": 500.0, "side": "long", "value": 20_000.0},        # flipped
             "ETH": {"size": 11.0, "side": "long", "value": 33_000.0},          # top-up, silent
             "SOL": {"size": -200.0, "side": "short", "value": 30_000.0},       # doubled
             "NEAR": {"size": 250.0, "side": "long", "value": 1_000.0, "leverage": 5.0}}  # opened
    kinds = {(c["kind"], c["coin"]) for c in pw.diff(before, after)}
    assert kinds == {("flipped", "ZEC"), ("closed", "BTC"), ("resized", "SOL"), ("opened", "NEAR")}


def test_describe_reads_on_a_phone():
    changes = pw.diff({"BTC": {"size": 0.1, "side": "long", "value": 7_000.0}},
                      {"NEAR": {"size": 250.0, "side": "long", "value": 1_000.0, "leverage": 5.0}})
    lines = sorted(pw.describe(c) for c in changes)
    assert lines == ["CLOSED long BTC 0.1 ($7K)", "OPENED long NEAR 250 ($1K 5x)"]


def test_render_is_one_notice_for_every_wallet_that_moved():
    changes = {W: pw.diff({}, {"ZEC": {"size": -500.0, "side": "short", "value": 2_500_000.0}}),
               W2: []}
    title, body = pw.render(changes, labels={W: "target"})
    assert title == "He moved: 1 position change(s)"
    assert body == "target:\n  OPENED short ZEC 500 ($2.50M)"
    assert pw.render({W: [], W2: []}) is None
    changes[W2] = pw.diff({"BTC": {"size": 1.0, "side": "long", "value": 1.0}}, {})
    title, _ = pw.render(changes)
    assert title.endswith("across 2 wallets")


def test_summarise_aggregates_his_book_across_wallets():
    books = {W: {"ZEC": {"size": -500.0, "value": 20_000.0}, "BTC": {"size": 1.0, "value": 70_000.0}},
             W2: {"ZEC": {"size": -100.0, "value": 4_000.0}, "BTC": {"size": -1.0, "value": 70_000.0}}}
    agg = pw.summarise(books)
    assert list(agg) == ["ZEC"], agg
    assert agg["ZEC"] == {"size": -600.0, "side": "short", "net_value": -24_000.0, "wallets": [W, W2]}
