"""When the identity sweep reads a wallet again: a week on, or before it had the portfolio fields.

`total_value` arrived with `parse_activity`. A non-core row probed earlier keeps the perp-margin
reading, and the study set judges Hyperliquid presence on the total, so such a row is re-read on
the next pass instead of up to RECHECK_DAYS later (a lead holding $9.37M in spot read as empty).
"""

from datetime import UTC, datetime, timedelta

import scripts.check_identity as ci

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def row(age, **fields):
    return {"checked_at": (NOW - age).isoformat(), **fields}


def test_a_fresh_row_with_no_total_value_key_is_stale():
    assert ci._stale(row(timedelta(hours=1)), NOW) is True
    assert ci._stale(row(timedelta(hours=1), account_value=0.0, birth_ms=5), NOW) is True
    assert ci._stale({}, NOW) is True                       # never probed


def test_a_fresh_row_with_the_key_is_not_stale_even_when_the_probe_stored_none():
    # The KEY marks a probe that has run since the fields existed. A probe whose portfolio
    # read failed stores None; re-reading it early would make that wallet a standing cost.
    for stored in (None, 0.0, 9_370_000.5):
        assert ci._stale(row(timedelta(hours=1), total_value=stored), NOW) is False


def test_an_old_row_is_stale_whatever_it_carries():
    assert ci._stale(row(timedelta(days=7), total_value=5.0), NOW) is True
    assert ci._stale(row(timedelta(days=30), total_value=None), NOW) is True
    assert ci._stale(row(timedelta(days=6, hours=23), total_value=5.0), NOW) is False


def test_a_row_with_no_usable_reading_time_is_stale():
    assert ci._stale({"total_value": 5.0}, NOW) is True
    assert ci._stale({"total_value": 5.0, "checked_at": "not a date"}, NOW) is True
    assert ci._stale({"total_value": 5.0, "checked_at": None}, NOW) is True
    assert ci._stale(None, NOW) is True
