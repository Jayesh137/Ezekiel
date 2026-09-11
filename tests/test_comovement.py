# tests/test_comovement.py
"""Lead/lag co-movement: a copier follows, a second hand leads or ties."""

from src import comovement as cm

MS = 60_000
DAY = 24 * 60


def _fills(events, coin="ETH", side="B"):
    """events: list of (start_minute, n_fills) bursts one minute apart."""
    return [{"coin": coin, "side": side, "time": (start + i) * MS}
            for start, n in events for i in range(n)]


def _irregular(k: int) -> int:
    """A decision time that does not repeat at the same clock time each day."""
    return k * DAY + (60 + 137 * k) % 1200


def test_fills_compress_into_decisions():
    fills = _fills([(0, 5), (10, 3), (100, 2)]) + _fills([(0, 1)], side="A")
    decs = cm.decisions(fills)
    assert [(d["coin"], d["side"], d["fills"]) for d in decs] == [
        ("ETH", "B", 8), ("ETH", "A", 1), ("ETH", "B", 2)]


def test_a_copier_follows_and_a_second_hand_leads():
    target, copier, same_hand = [], [], []
    for k in range(14):
        target += _fills([(_irregular(k), 3)])
        copier += _fills([(_irregular(k) + 5, 1)])       # five minutes after, every time
        same_hand += _fills([(_irregular(k) - 2, 1)])    # two minutes before, every time
    assert cm.score(target, copier)["verdict"] == "copier"
    got = cm.score(target, same_hand)
    assert got["verdict"] == "same_hand" and got["lead_share"] == 1.0


def test_too_few_pairs_is_untestable_not_a_verdict():
    target = []
    for k in range(14):
        target += _fills([(_irregular(k), 3)])
    stranger = _fills([(_irregular(7) - 30, 1), (3 * DAY + 900, 1)])
    got = cm.score(target, stranger)
    assert got["verdict"] == "untestable" and got["pairs"] <= 1


def test_the_control_shift_removes_a_daily_coincidence():
    """A wallet that happens to trade the same coin at the same hour every day
    pairs with the target — and pairs just as well when shifted by a day."""
    target, other = [], []
    for k in range(20):
        target += _fills([(k * DAY + 60, 2)])
        other += _fills([(k * DAY + 61, 1)])
    got = cm.score(target, other)
    assert got["paired_share"] == 1.0 and got["control_share"] >= 0.9
    assert got["verdict"] == "independent"


def test_empty_sides_are_untestable():
    assert cm.score([], _fills([(0, 1)]))["verdict"] == "untestable"
    assert cm.score(_fills([(0, 1)]), [])["verdict"] == "untestable"


def test_report_orders_same_hand_first():
    target, lead = [], []
    for k in range(14):
        target += _fills([(_irregular(k), 3)])
        lead += _fills([(_irregular(k) - 1, 1)])
    report = cm.build_report(target, {"0xB": _fills([(5, 1)]), "0xA": lead})
    assert list(report["results"]) == ["0xa", "0xb"]
    assert report["results"]["0xa"]["verdict"] == "same_hand"


def test_every_verdict_carries_the_same_fields():
    """A missing key renders as None, which reads like a failed measurement
    rather than a measured nothing."""
    no_overlap = cm.score_decisions([], [{"coin": "ETH", "side": "B", "start": 1, "end": 2}])
    assert no_overlap["pairs"] == 0 and no_overlap["verdict"] == "untestable"
