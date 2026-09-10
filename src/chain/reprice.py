# src/chain/reprice.py
"""Filling in prices for records that were stored before one was available.

Pricing happens once, at collection time, inside `collect.normalise_row`. That
is the right place for it, but it makes coverage a snapshot of what the price
cache happened to hold at the moment a record was first seen — and the price
budget is deliberately tiny (a dozen requests per run against a free tier that
rate-limits), so a sweep that collects thousands of records prices only a
handful of them.

Without this module, everything else stays `price_unavailable` forever. It sits
on disk with `amount` and `asset` intact but `amount_usd: None`, so
`transfer_graph.normalise_transfer_record` drops it and it never becomes a
graph edge. The "coverage improves incrementally across runs" design that
justifies the small budget only half works: the cache fills, and nothing goes
back to use it.

This is the other half. It re-reads stored records, retries the ones that were
never priced, and rewrites them in place when a price is now available.

Two things make a tiny budget go a long way:

  * **Lookups are grouped by (asset, date), not done per record.** Transfers
    cluster hard — a day of activity in one asset is one price, however many
    transfers it covers. One request can therefore reprice many records, and
    the same group is never looked up twice in a run even across chains.
  * **A group that comes back unpriced is not retried within the run.** The
    price source already distinguishes a definitive miss (cached, never
    re-requested) from an indeterminate one (not cached, retryable next run);
    this module does not need to re-derive that distinction, only avoid
    spending its budget re-asking the same question.

Idempotent by construction: a record only changes when a real price arrives, so
a second pass over an already-priced file rewrites nothing.

**This is the only code in the project that mutates already-stored records.**
Everything else appends (deduped by `id`) or writes a fresh `latest.json`. That
makes it the one place a read-modify-write race could lose data: two processes
reading the same daily file, each editing its own copy, the second overwriting
the first's addition. `atomic_write_json` does not prevent that — it makes each
individual write atomic, not the read-then-write pair.

What prevents it is that every workflow touching `data/` shares GitHub Actions'
`data-commit` concurrency group, so they are serialised. That guarantee does not
extend to a local invocation: do not run this by hand while a sweep is running.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.chain.assets import value_usd
from src.chain.collect import TRANSFERS_DIR
from src.utils import atomic_write_json

# The basis a record carries when its asset is one we price but no price was
# available at collection time. `unpriced` — an asset we do not price at all —
# is deliberately NOT repriced: that is a definitive verdict about the token,
# not a gap in coverage, and retrying it every run would spend budget on
# scam airdrops forever.
REPRICEABLE_BASIS = "price_unavailable"


def _date_of(rec: dict) -> str:
    """The record's UTC date, derived the same way `normalise_row` derives it.

    Deriving it from `ts` rather than trusting the stored `timestamp` string
    keeps the two in step: a record repriced here must key into exactly the
    same (symbol, date) cache entry it would have used at collection time, or
    the cache silently splits and the request is spent twice.
    """
    try:
        return datetime.fromtimestamp(int(rec.get("ts", 0) or 0), tz=UTC).isoformat()[:10]
    except (TypeError, ValueError, OSError):
        return ""


def _out_of_price_window(date_str: str) -> bool:
    """Is this date beyond what the configured price source can serve?

    Mirrors the same check prices.py applies before it spends a request, so the
    two cannot disagree about which dates are reachable. With a key configured
    the window does not apply, and nothing is reported as permanently
    unpriceable.
    """
    from src.chain.prices import (
        DEMO_KEY_ENV_VAR,
        FREE_TIER_HISTORY_DAYS,
        _too_old,
    )
    if os.environ.get(DEMO_KEY_ENV_VAR, ""):
        return False
    return _too_old(date_str, FREE_TIER_HISTORY_DAYS)


def _needs_price(rec: dict) -> bool:
    return (not rec.get("spam")
            and rec.get("amount_usd") is None
            and rec.get("value_basis") == REPRICEABLE_BASIS)


def reprice_stored_records(price_lookup, *, root=None) -> dict:
    """Retry the price on every stored record that never got one.

    `price_lookup(symbol, date_str) -> float | None` is injected, exactly as
    `sweep_wallet` takes it, so this is testable without a network and shares
    the caller's request budget: once that budget is spent the lookup simply
    returns None for everything after, and this pass ends up a no-op rather
    than overrunning a job.

    Returns a health dict. `still_unpriced` is not a failure — it is the
    honest count of what a bounded run could not reach, and the number that
    should shrink across runs as the cache fills.
    """
    root = Path(root if root is not None else TRANSFERS_DIR)
    health = {
        "computed_at": datetime.now(UTC).isoformat(),
        "examined": 0,
        "repriced": 0,
        "still_unpriced": 0,
        # Of `still_unpriced`, the part that will NEVER price on the current
        # key tier because the date predates the price source's history window.
        # Without this split the residual conflates "not reached yet" with
        # "unreachable", so the one number an operator watches cannot go to
        # zero and the advice to watch it trend down is unactionable. Measured
        # live: 1,164 of 1,359 unpriced records predated the free tier's
        # 365-day window and were counted as a growing backlog every run.
        "unpriceable_out_of_window": 0,
        "groups_tried": 0,
        "files_rewritten": 0,
        # A file we could not read is blindness, not absence. Without this the
        # only observable signal — `still_unpriced` trending down — looks
        # identical whether the cache has caught up or a daily file has been
        # permanently unreadable for a week.
        "files_unreadable": [],
    }
    if not root.exists():
        return health

    # (asset, date) -> price or None, for this run only. Shared across chains
    # and files: the same asset on the same day is one price everywhere, so a
    # single request can reprice every record in that group.
    seen: dict[tuple[str, str], float | None] = {}

    def memo(symbol: str, date_str: str) -> float | None:
        """`price_lookup`, asked at most once per (symbol, date) per run.

        A group that comes back unpriced is not re-asked either. The price
        source already decides whether a miss is definitive (cached, never
        re-requested) or indeterminate (not cached, retryable next run); this
        only avoids spending a bounded budget re-asking inside one pass.
        """
        key = (symbol, date_str)
        if key not in seen:
            seen[key] = price_lookup(symbol, date_str)
            health["groups_tried"] += 1
        return seen[key]

    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(chain_dir.glob("*.json")):
            try:
                records = json.loads(path.read_text())
            except (OSError, ValueError):
                # A file we cannot read is not a file we may rewrite. Leave it
                # exactly as it is — and say so, rather than skipping silently.
                health["files_unreadable"].append(str(path))
                continue
            if not isinstance(records, list):
                health["files_unreadable"].append(str(path))
                continue

            changed = False
            for rec in records:
                if not isinstance(rec, dict) or not _needs_price(rec):
                    continue
                health["examined"] += 1

                symbol = (rec.get("asset") or "").strip().upper()
                date_str = _date_of(rec)
                if not symbol or not date_str:
                    health["still_unpriced"] += 1
                    continue

                # Valued through `value_usd`, not by repeating its arithmetic
                # here: the basis strings and the rounding are its contract,
                # and a second copy of them is how the collection path and
                # this one drift into disagreeing about the same transfer.
                amount_usd, basis = value_usd(
                    symbol, rec.get("amount", 0.0), date_str, memo)

                if amount_usd is None:
                    health["still_unpriced"] += 1
                    if _out_of_price_window(date_str):
                        health["unpriceable_out_of_window"] += 1
                    continue

                # Only ever widened from None to a real number. A price that
                # did not arrive leaves the record byte-identical, which is
                # what makes a second pass a no-op.
                rec["amount_usd"] = amount_usd
                rec["value_basis"] = basis
                health["repriced"] += 1
                changed = True

            if changed:
                atomic_write_json(path, records)
                health["files_rewritten"] += 1

    return health
