# tests/test_substrate_sharding.py
"""No single substrate file may approach GitHub's blob limit. Ever.

Run 35082536547 (2026-09-16) swept 25 wallets, stored 345,100 genuinely new
records — the `known_ids` dedupe correctly held back all 615,375 we already
had — and then died in `Commit and push`:

    [size] OVER LIMIT data/transfers/ethereum/2026-09-16.json is 233.74 MiB

`check_repo_size.py` refused the push, so nothing was committed and **the whole
run's work was discarded**: 1,229 API calls and 25 minutes, gone. The guard did
its job; the design behind it did not. A day's collection was one file, so the
only bound on that file was how much the sweep happened to collect.

Sharding removes the failure mode rather than detecting it. A day is a SET of
files — `2026-09-16.json`, `2026-09-16.p2.json`, … — and the writer rolls to
the next shard before the current one gets big. Nothing else has to change:
`substrate_files` already keys a day off the filename, so shards are simply
more days as far as every reader is concerned.

Two properties this pins, because getting either wrong re-creates the bug:

1. **A single append is itself split.** Rolling only BETWEEN appends leaves the
   hole open: one wallet-chain can legitimately return
   `max_pages_per_kind` x `page_size` x 3 kinds = 150,000 rows, which at the
   measured 683 bytes a record is ~102 MB in one write — over the limit on a
   fresh, empty shard.
2. **Dedupe still covers the whole day.** `append_records` dedupes against the
   one file it writes. Split the day naively and a record already in
   `<date>.json` is re-stored in `<date>.p2.json`, which is the duplication the
   `known_ids` work just removed, reintroduced one layer down.
"""

import json

import pytest

from src.chain import collect


@pytest.fixture
def chain_dir(tmp_path, monkeypatch):
    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    collect._FILE_CACHE.clear()
    return root / "ethereum"


def _records(n, start=0):
    return [{"id": f"ethereum:0x{i:064x}:erc20:0", "src": "0xa", "dst": "0xb",
             "asset": "USDC", "amount_usd": 1.0, "spam": False,
             # padding so a record has realistic weight without needing 150k of them
             "pad": "x" * 600}
            for i in range(start, start + n)]


def _all_stored(chain_dir):
    out = []
    for p in sorted(chain_dir.glob("*.json")):
        out.extend(json.loads(p.read_text()))
    return out


def test_one_oversized_append_is_split_across_shards(chain_dir, monkeypatch):
    """The hole that rolling only between appends would leave open."""
    monkeypatch.setattr(collect, "SHARD_MAX_BYTES", 50_000)

    collect.append_transfer_records(str(chain_dir), _records(300))

    sizes = [p.stat().st_size for p in chain_dir.glob("*.json")]
    assert len(sizes) > 1, "a single large append stayed in one file"
    assert max(sizes) < 100_000, f"a shard grew to {max(sizes)}"


def test_every_record_survives_the_split(chain_dir, monkeypatch):
    monkeypatch.setattr(collect, "SHARD_MAX_BYTES", 50_000)

    collect.append_transfer_records(str(chain_dir), _records(300))

    assert len(_all_stored(chain_dir)) == 300


def test_a_record_already_in_an_earlier_shard_is_not_re_stored(chain_dir, monkeypatch):
    """Dedupe covers the whole DAY, not just the file being written.

    Splitting the day without this reintroduces exactly the duplication the
    known_ids work removed, one layer further down.
    """
    monkeypatch.setattr(collect, "SHARD_MAX_BYTES", 50_000)
    collect.append_transfer_records(str(chain_dir), _records(300))
    before = len(_all_stored(chain_dir))

    collect.append_transfer_records(str(chain_dir), _records(300))

    assert len(_all_stored(chain_dir)) == before


def test_a_small_append_stays_in_the_first_file(chain_dir):
    """Unsharded is the normal case and must stay the normal case."""
    collect.append_transfer_records(str(chain_dir), _records(3))

    assert [p.name for p in chain_dir.glob("*.json")] == [
        f"{collect.today_str()}.json"]


def test_every_shard_is_read_back_by_the_substrate_reader(chain_dir, monkeypatch):
    """A shard nobody reads is worse than a file that failed to push."""
    monkeypatch.setattr(collect, "SHARD_MAX_BYTES", 50_000)
    collect.append_transfer_records(str(chain_dir), _records(300))

    seen = []
    for path in collect.substrate_files(chain_dir):
        seen.extend(collect.read_records(path))

    assert len(seen) == 300


def test_records_for_sees_a_sharded_day(chain_dir, monkeypatch):
    monkeypatch.setattr(collect, "SHARD_MAX_BYTES", 50_000)
    collect.append_transfer_records(str(chain_dir), _records(300))
    collect._FILE_CACHE.clear()

    assert len(collect.records_for("0xb")) == 300


def test_no_shard_ever_approaches_the_blob_limit(chain_dir):
    """The property the whole file exists for, at the real threshold."""
    assert collect.SHARD_MAX_BYTES < 100 * 1024 * 1024
    # Room for one more append on top of a shard already at the bar.
    assert collect.SHARD_MAX_BYTES * 2 < 100 * 1024 * 1024


def test_a_sealed_shard_is_compacted_like_any_other_day(tmp_path, monkeypatch):
    """A shard must roll to .jsonl.gz too, or sharding trades one size
    problem for a slower one: uncompacted shards accumulate for ever."""
    import scripts.compact_data as cd

    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    collect._FILE_CACHE.clear()

    recs = _records(5)
    (root / "ethereum" / "2026-09-10.json").write_text(json.dumps(recs[:2]))
    (root / "ethereum" / "2026-09-10.p2.json").write_text(json.dumps(recs[2:]))

    cd.compact_transfers(dry_run=False)

    names = sorted(p.name for p in (root / "ethereum").iterdir())
    assert names == ["2026-09-10.jsonl.gz", "2026-09-10.p2.jsonl.gz"]

    seen = []
    for path in collect.substrate_files(root / "ethereum"):
        seen.extend(collect.read_records(path))
    assert len(seen) == 5


def test_todays_shard_is_never_compacted(tmp_path, monkeypatch):
    """The collector is still appending to it."""
    import scripts.compact_data as cd

    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path)
    today = collect.today_str()
    (root / "ethereum" / f"{today}.p2.json").write_text(json.dumps(_records(2)))

    cd.compact_transfers(dry_run=False)

    assert (root / "ethereum" / f"{today}.p2.json").exists()
