# tests/test_spam_rollup_trim.py
"""The quarantine ledger must stay small without starting to lie.

`data/transfers_spam/latest.json` is the record of what the sweep threw away:
130,099 address entries covering 611,811 suppressed records on 2026-09-16. It
reached **38.9 MB, the largest file in the repo**, growing ~5 MB a day — about
thirteen days from GitHub's 100 MiB blob limit, which refuses the push AFTER a
run has done its work. It is also rewritten WHOLE on every run, 200 commits so
far, so each run adds another ~38 MB blob.

And nothing reads it. Not `src/`, not `scripts/`, not the dashboard, not
`data/index.json`. The same shape as the transfer graph carrying 212,457 edges
to serve a maximum of 40.

Trimming is therefore right, but it moves one number from exact to wrong unless
it is restructured first. `suppressed_total` was `sum(e["count"] for e in
merged)` — a sum over the STORED list. Evict the tail and the total silently
shrinks, so the ledger of our own blindness would under-report the moment it
started saving space. It becomes a persisted running total instead, seeded from
the legacy sum, and the same for the per-reason breakdown.

What CANNOT be maintained exactly once entries are evicted is the number of
distinct addresses ever seen: a re-seen evicted address is indistinguishable
from a new one. So no such field is published. Rule 5 cuts both ways — a
number that cannot be exact must not be presented as though it were.

`spam.rollup`'s docstring gives the file its one human purpose: `asset` and
`token_address` are kept "so a legitimate token the registry does not yet know
is visible and can be added to assets.py". Keeping the entries with the highest
counts serves exactly that — a token worth adding to the registry is one that
keeps turning up.
"""

import json

import pytest

from src.chain import collect


@pytest.fixture
def spam_dir(tmp_path, monkeypatch):
    d = tmp_path / "transfers_spam"
    d.mkdir(parents=True)
    monkeypatch.setattr(collect, "SPAM_DIR", d)
    return d


def _entry(addr, count, reason="dust", ts=1000):
    return {"address": addr, "reason": reason, "mimics": None, "asset": "JUNK",
            "token_address": "0xtok", "count": count,
            "first_seen": ts, "last_seen": ts}


def _stored(spam_dir):
    return json.loads((spam_dir / "latest.json").read_text())


def _many(n, start=0, count=1):
    return [_entry(f"0x{i:040x}", count) for i in range(start, start + n)]


def test_the_stored_entry_list_is_capped(spam_dir):
    collect._merge_spam_rollup(_many(collect.MAX_SPAM_ENTRIES + 500))

    assert len(_stored(spam_dir)["entries"]) == collect.MAX_SPAM_ENTRIES


def test_the_loudest_offenders_are_the_ones_kept(spam_dir):
    """Frequency is what makes an entry worth keeping.

    A token worth adding to assets.py is one that keeps turning up; a
    single-sighting address is the 61.5% of this file that serves nobody.
    """
    entries = _many(collect.MAX_SPAM_ENTRIES, count=1) + [_entry("0xloud", 9999)]

    collect._merge_spam_rollup(entries)

    kept = {e["address"] for e in _stored(spam_dir)["entries"]}
    assert "0xloud" in kept


def test_the_suppressed_total_counts_evicted_entries_too(spam_dir):
    """The number that must NOT shrink when the file does.

    Summing the stored list was correct only while the list was complete. This
    is the ledger of what we threw away; under-reporting it to save space would
    make the saving cost the only thing the file is for.
    """
    collect._merge_spam_rollup(_many(collect.MAX_SPAM_ENTRIES + 500, count=1))

    stored = _stored(spam_dir)
    assert stored["suppressed_total"] == collect.MAX_SPAM_ENTRIES + 500
    assert len(stored["entries"]) == collect.MAX_SPAM_ENTRIES


def test_the_total_accumulates_across_runs(spam_dir):
    collect._merge_spam_rollup([_entry("0xa", 3)])
    collect._merge_spam_rollup([_entry("0xa", 4), _entry("0xb", 5)])

    assert _stored(spam_dir)["suppressed_total"] == 12


def test_a_re_seen_evicted_address_does_not_double_count(spam_dir):
    """Eviction resets an address's own count, never the running total."""
    collect._merge_spam_rollup(_many(collect.MAX_SPAM_ENTRIES, count=50))
    collect._merge_spam_rollup([_entry("0xquiet", 1)])
    collect._merge_spam_rollup([_entry("0xquiet", 1)])

    stored = _stored(spam_dir)
    expected = collect.MAX_SPAM_ENTRIES * 50 + 2
    assert stored["suppressed_total"] == expected


def test_per_reason_totals_are_exact_after_trimming(spam_dir):
    entries = ([_entry(f"0x{i:040x}", 2, reason="unpriced_token")
                for i in range(collect.MAX_SPAM_ENTRIES + 100)]
               + [_entry("0xdust1", 7, reason="dust")])

    collect._merge_spam_rollup(entries)

    by_reason = _stored(spam_dir)["by_reason"]
    assert by_reason["unpriced_token"] == (collect.MAX_SPAM_ENTRIES + 100) * 2
    assert by_reason["dust"] == 7


def test_truncation_is_declared(spam_dir):
    """A kept list must never be mistakable for the whole list."""
    collect._merge_spam_rollup(_many(collect.MAX_SPAM_ENTRIES + 1))

    stored = _stored(spam_dir)
    assert stored["entries_truncated"] is True
    assert stored["entries_stored"] == collect.MAX_SPAM_ENTRIES
    assert stored["entries_cap"] == collect.MAX_SPAM_ENTRIES


def test_an_untruncated_file_says_so(spam_dir):
    collect._merge_spam_rollup([_entry("0xa", 1)])

    stored = _stored(spam_dir)
    assert stored["entries_truncated"] is False
    assert stored["entries_stored"] == 1


def test_a_legacy_file_carries_its_total_forward(spam_dir):
    """Migration: the old `suppressed_total` WAS the running total.

    It was a sum over a complete list, so it is exactly the figure to seed
    with — no recount, and the live 611,811 survives the first trimmed write.
    """
    (spam_dir / "latest.json").write_text(json.dumps({
        "last_updated": "2026-09-16T00:00:00+00:00",
        "suppressed_total": 611811,
        "entries": [_entry("0xold", 611811)],
    }))

    collect._merge_spam_rollup([_entry("0xnew", 5)])

    assert _stored(spam_dir)["suppressed_total"] == 611816


def test_a_legacy_file_seeds_per_reason_totals_from_its_entries(spam_dir):
    (spam_dir / "latest.json").write_text(json.dumps({
        "suppressed_total": 30,
        "entries": [_entry("0xold", 30, reason="zero_value")],
    }))

    collect._merge_spam_rollup([_entry("0xnew", 5, reason="dust")])

    by_reason = _stored(spam_dir)["by_reason"]
    assert by_reason == {"zero_value": 30, "dust": 5}


def test_counts_for_a_surviving_address_still_accumulate(spam_dir):
    collect._merge_spam_rollup([_entry("0xa", 3, ts=100)])
    collect._merge_spam_rollup([_entry("0xa", 4, ts=900)])

    kept = _stored(spam_dir)["entries"][0]
    assert kept["count"] == 7
    assert kept["first_seen"] == 100
    assert kept["last_seen"] == 900
