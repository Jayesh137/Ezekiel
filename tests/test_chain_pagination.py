from src.chain.pagination import walk_blocks


def row(block, h, log=""):
    return {"blockNumber": str(block), "hash": h, "logIndex": log}


def test_a_short_page_ends_the_walk():
    pages = [[row(10, "a"), row(11, "b")]]
    got = walk_blocks(lambda s, n: pages.pop(0), 0, page_size=5)
    assert [r["hash"] for r in got.rows] == ["a", "b"]
    assert got.pages == 1
    assert got.last_block == 11
    assert got.truncated is False
    assert got.possible_gaps == []


def test_a_full_page_advances_the_start_block_and_continues():
    calls = []

    def fetch(start, n):
        calls.append(start)
        if start == 0:
            return [row(5, "a"), row(9, "b")]
        return [row(12, "c")]

    got = walk_blocks(fetch, 0, page_size=2)
    assert calls == [0, 9]                       # resumed from the highest block seen
    assert [r["hash"] for r in got.rows] == ["a", "b", "c"]
    assert got.pages == 2
    assert got.last_block == 12


def test_boundary_duplicates_collapse_by_row_key():
    def fetch(start, n):
        if start == 0:
            return [row(5, "a"), row(9, "b")]
        return [row(9, "b"), row(12, "c")]       # b repeats across the boundary

    got = walk_blocks(fetch, 0, page_size=2)
    assert [r["hash"] for r in got.rows] == ["a", "b", "c"]


def test_same_block_stall_advances_by_one_and_records_a_possible_gap():
    """A single block holding more than page_size rows would otherwise loop
    forever on the same start_block, or silently drop the overflow."""
    calls = []

    def fetch(start, n):
        calls.append(start)
        if start == 0:
            return [row(7, "a"), row(7, "b")]    # full page, all one block
        return []

    got = walk_blocks(fetch, 0, page_size=2)
    assert calls == [0, 8]                       # advanced past the stalled block
    assert got.possible_gaps == [7]


def test_max_pages_truncates_and_says_so():
    def fetch(start, n):
        return [row(start + 1, f"h{start}"), row(start + 2, f"i{start}")]

    got = walk_blocks(fetch, 0, page_size=2, max_pages=3)
    assert got.pages == 3
    assert got.truncated is True


def test_rows_without_a_usable_block_number_do_not_stall_the_walk():
    def fetch(start, n):
        if start == 0:
            return [{"hash": "a", "blockNumber": "not-a-number"}, row(4, "b")]
        return []

    got = walk_blocks(fetch, 0, page_size=2)
    assert [r["hash"] for r in got.rows] == ["a", "b"]
    assert got.last_block == 4


def test_an_empty_first_page_returns_cleanly():
    got = walk_blocks(lambda s, n: [], 100, page_size=10)
    assert got.rows == []
    assert got.pages == 1
    assert got.last_block == 100
    assert got.truncated is False
