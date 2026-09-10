# tests/test_order_profile.py
"""How a trader SUBMITS orders, not just what filled.

54,866 historical order records were collected and never read. Fills are the
shadow of an order: they show what happened, not how it was asked for. Cancel
rates, time-in-force, trigger use and client order ids are habits, and habits
travel with a person to a new wallet.

Measured 2026-09-10 this separates the target from every current lead:

    target        94.6% Ioc,   0% cancels,   0% client order ids
    three leads      0% Ioc, ~100% cancels, 100% client order ids

He places immediate-or-cancel slices by hand. They are bots.
"""

import pytest

from src.fingerprint import compute_order_profile
from src.scanner import compare_order_profile


def _order(status="filled", order_type="Limit", tif="Ioc", **over):
    o = {"coin": "BTC", "orderType": order_type, "tif": tif,
         "reduceOnly": False, "isTrigger": False, "cloid": None}
    o.update(over)
    return {"oid": 1, "order": o, "status": status}


def test_profile_measures_the_submission_habits():
    got = compute_order_profile([
        _order(), _order(), _order(status="canceled"), _order(cloid="0xabc"),
    ])
    assert got["orders"] == 4
    assert got["cancel_rate"] == 0.25
    assert got["programmatic_rate"] == 0.25
    assert got["tif_mix"]["Ioc"] == 1.0


def test_no_orders_is_reported_as_no_orders():
    """Distinguishable from 'measured and all zero' — a caller checks `orders`."""
    got = compute_order_profile([])
    assert got["orders"] == 0
    assert "cancel_rate" not in got


def test_junk_records_are_skipped():
    assert compute_order_profile([None, "nope", {"no": "order"}, 7])["orders"] == 0


def test_identical_habits_score_near_one():
    a = compute_order_profile([_order() for _ in range(10)])
    assert compare_order_profile({"order_profile": a},
                                 {"order_profile": a}) > 0.99


def test_a_manual_slicer_and_a_cancelling_bot_are_far_apart():
    """The live case. The target never cancels and uses no client order ids;
    every current lead cancels ~100% of orders and sets one on every order."""
    manual = compute_order_profile(
        [_order(tif="Ioc", order_type="Limit") for _ in range(100)])
    bot = compute_order_profile(
        [_order(status="canceled", tif="Gtc", cloid=f"0x{i:x}") for i in range(100)])
    score = compare_order_profile({"order_profile": manual},
                                  {"order_profile": bot})
    assert score < 0.5


def test_missing_order_data_redistributes_rather_than_voting_against():
    """A candidate whose orders were never fetched must not be penalised for
    data nobody asked for — the same rule hold_duration follows."""
    a = compute_order_profile([_order()])
    assert compare_order_profile({"order_profile": a}, {"order_profile": {}}) is None
    assert compare_order_profile({"order_profile": {}}, {"order_profile": a}) is None
    assert compare_order_profile({}, {}) is None


def test_a_category_on_one_side_only_counts_against_the_match():
    """Cosine over the UNION of keys. Dropping unmatched categories would let a
    trader who does something the other never does still score a perfect match."""
    a = compute_order_profile([_order(order_type="Limit") for _ in range(10)])
    b = compute_order_profile([_order(order_type="Market") for _ in range(10)])
    score = compare_order_profile({"order_profile": a}, {"order_profile": b})
    # One of seven components differs, so ~0.857 — the point is that it is
    # BELOW a perfect match. Dropping unmatched categories would score 1.0.
    assert score < 1.0
    assert score == pytest.approx(6 / 7, abs=0.01)
