# tests/test_accounting.py
"""Of everything that left the target, how much do we know the destination of?

Without this, "follow every dollar" is unfalsifiable: the graph can look healthy
while most of the money went somewhere nothing on the dashboard mentions.

Two ways a reconciliation lies to itself, both pinned here — counting unpriced
transfers as $0, and counting leads as answers.
"""

import src.accounting as acc

TARGET = "0x" + "11" * 20
SELF = "0x" + "22" * 20
CONFIRMED = "0x" + "33" * 20
SERVICE = "0x" + "44" * 20
LEAD = "0x" + "55" * 20
STRANGER = "0x" + "66" * 20

ROSTER = {
    CONFIRMED: {"tier": "CONFIRMED"},
    SERVICE: {"tier": "INFRASTRUCTURE"},
    LEAD: {"tier": "POSSIBLE"},
}


def _rec(dst, usd, asset="USDC", src=TARGET):
    return {"src": src, "dst": dst, "amount_usd": usd, "asset": asset}


def test_each_destination_lands_in_the_right_bucket():
    out = acc.reconcile(
        [_rec(SELF, 100.0), _rec(CONFIRMED, 200.0), _rec(SERVICE, 300.0),
         _rec(LEAD, 400.0), _rec(STRANGER, 500.0)],
        TARGET, ROSTER, {SELF})
    b = out["buckets"]
    assert b[acc.DEST_SELF]["usd"] == 100.0
    assert b[acc.DEST_IDENTIFIED]["usd"] == 200.0
    assert b[acc.DEST_INFRASTRUCTURE]["usd"] == 300.0
    assert b[acc.DEST_LEAD]["usd"] == 400.0
    assert b[acc.DEST_UNKNOWN]["usd"] == 500.0
    assert out["total_out_usd"] == 1500.0


def test_leads_are_not_counted_as_traced():
    """A POSSIBLE wallet is a question. Counting questions as answers is how a
    reconciliation flatters itself into reporting near-total coverage."""
    out = acc.reconcile([_rec(SERVICE, 100.0), _rec(LEAD, 900.0)],
                        TARGET, ROSTER, set())
    assert out["traced_usd"] == 100.0
    assert out["traced_share"] == 0.1


def test_an_unpriced_transfer_is_counted_but_never_valued():
    """Folding a None price in as $0 reports a tidy total that omits real money
    — the same failure as a missing price becoming a $0 transfer."""
    out = acc.reconcile(
        [_rec(SERVICE, 100.0), _rec(STRANGER, None, asset="ETH")],
        TARGET, ROSTER, set())
    assert out["total_out_usd"] == 100.0          # the None did NOT add 0.0
    assert out["unpriced"]["transfers"] == 1
    assert out["unpriced"]["assets"] == {"ETH": 1}
    assert out["unpriced"]["distinct_wallets"] == 1
    # And it did not silently become an "unknown" dollar either.
    assert out["buckets"][acc.DEST_UNKNOWN]["usd"] == 0.0


def test_inbound_transfers_are_ignored():
    """This measures what LEFT. Money arriving is a different question."""
    out = acc.reconcile([_rec(TARGET, 500.0, src=STRANGER)], TARGET, ROSTER, set())
    assert out["total_out_usd"] == 0.0


def test_repeated_transfers_to_one_wallet_count_once_as_a_wallet():
    out = acc.reconcile([_rec(LEAD, 10.0), _rec(LEAD, 20.0), _rec(LEAD, 30.0)],
                        TARGET, ROSTER, set())
    b = out["buckets"][acc.DEST_LEAD]
    assert b["usd"] == 60.0
    assert b["transfers"] == 3
    assert b["distinct_wallets"] == 1


def test_shares_sum_to_one_when_anything_moved():
    out = acc.reconcile([_rec(SERVICE, 250.0), _rec(LEAD, 750.0)],
                        TARGET, ROSTER, set())
    assert round(sum(b["share"] for b in out["buckets"].values()), 6) == 1.0


def test_no_outflow_does_not_divide_by_zero():
    out = acc.reconcile([], TARGET, ROSTER, set())
    assert out["total_out_usd"] == 0.0
    assert out["traced_share"] == 0.0
    assert all(b["share"] == 0.0 for b in out["buckets"].values())


def test_a_malformed_amount_is_skipped_not_counted_as_zero():
    out = acc.reconcile([_rec(SERVICE, "not-a-number"), _rec(SERVICE, 50.0)],
                        TARGET, ROSTER, set())
    assert out["total_out_usd"] == 50.0
    assert out["buckets"][acc.DEST_INFRASTRUCTURE]["transfers"] == 1


def test_known_self_outranks_the_roster():
    """Operator ground truth beats measurement, even if the roster disagrees."""
    roster = {SELF: {"tier": "POSSIBLE"}}
    out = acc.reconcile([_rec(SELF, 100.0)], TARGET, roster, {SELF})
    assert out["buckets"][acc.DEST_SELF]["usd"] == 100.0
    assert out["buckets"][acc.DEST_LEAD]["usd"] == 0.0


def test_a_wallet_absent_from_the_roster_is_unknown_not_traced():
    assert acc.classify_destination(STRANGER, {}, set()) == acc.DEST_UNKNOWN


def test_a_transfer_with_no_destination_is_dropped():
    out = acc.reconcile([{"src": TARGET, "dst": "", "amount_usd": 99.0}],
                        TARGET, ROSTER, set())
    assert out["total_out_usd"] == 0.0
