"""Walk an address's history by block range instead of by page number.

Etherscan caps any single query at 10,000 results, so `page=N` paging hits a
wall and silently stops. The collection this replaces asked for `page=1,
offset=1000, sort=desc` exactly once, which is why data/l1_transactions holds
exactly 1000 records and no history older than the newest 1000 transfers —
905 of which were address-poisoning dust.

Walking forward by block instead has no ceiling: ask from `start`, take the
highest block returned, ask again. The overlap this creates at each boundary is
absorbed by deduplication on a row key.

Pure: `fetch` is injected, so every branch — including the pathological
single-block stall — is testable without a network.
"""

from collections.abc import Callable
from typing import NamedTuple


class WalkResult(NamedTuple):
    rows: list[dict]
    last_block: int
    pages: int
    truncated: bool
    possible_gaps: list[int]


def default_row_key(row: dict) -> tuple:
    """Identity of an Etherscan row across the three record kinds.

    tokentx is unique on (hash, logIndex); txlist on hash alone; txlistinternal
    on (hash, traceId). Combining all three is unique for every kind without
    needing to know which kind produced the row.
    """
    return (
        str(row.get("hash", "")),
        str(row.get("logIndex", "")),
        str(row.get("traceId", "")),
    )


def _block_of(row: dict) -> int | None:
    try:
        return int(row.get("blockNumber"))
    except (TypeError, ValueError):
        return None


def walk_blocks(fetch: Callable[[int, int], list[dict]], start_block: int, *,
                page_size: int = 1000, max_pages: int = 50,
                key: Callable[[dict], tuple] = default_row_key) -> WalkResult:
    """Collect every row from `start_block` forward, in page_size chunks."""
    rows: list[dict] = []
    seen: set[tuple] = set()
    gaps: list[int] = []
    start = int(start_block)
    highest = start
    pages = 0
    truncated = False

    while pages < max_pages:
        page = fetch(start, page_size)
        pages += 1

        for row in page:
            k = key(row)
            if k in seen:
                continue
            seen.add(k)
            rows.append(row)
            block = _block_of(row)
            if block is not None and block > highest:
                highest = block

        if len(page) < page_size:
            break

        page_blocks = [b for b in (_block_of(r) for r in page) if b is not None]
        if page_blocks and len(set(page_blocks)) == 1:
            # All rows in this full page are from a single block.
            # There may be more rows in that block that we couldn't fetch.
            block = page_blocks[0]
            gaps.append(block)
            next_start = block + 1
        else:
            next_start = max(page_blocks) if page_blocks else start
            if next_start <= start:
                # One block holds at least a full page. Staying here loops forever;
                # stepping over it is the only way forward, and the step is recorded
                # because rows in that block beyond page_size are unreachable.
                gaps.append(start)
                next_start = start + 1
        start = next_start
    else:
        truncated = True

    return WalkResult(rows=rows, last_block=highest, pages=pages,
                      truncated=truncated, possible_gaps=gaps)
