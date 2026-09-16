# tests/test_substrate_compaction.py
"""`data/transfers/` must survive being stored gzipped, byte-for-record.

The substrate is the one store that cannot be re-derived from anything else in
the repo: `collect_known_edges()` rebuilds the entire transfer graph from it,
and re-sweeping it costs Etherscan budget nobody has. It is also the fastest
growing thing here — 416 MB in the seven days since 2026-09-09, with
`ethereum/2026-09-10.json` alone at 76.69 MB against GitHub's 100 MiB hard
blob limit. A single busy day crossing that line stops every committing
workflow, which is the failure that cost twelve consecutive trace runs when
the graph file crossed it on 2026-09-12.

Measured on that file: 76.69 MB of JSON becomes 5.20 MB of gzipped JSONL, 14.8x,
and reads back in 0.25s. Across the whole substrate 416 MB becomes 68.3 MB.

So compaction is lossless re-encoding, and every test here exists to hold it to
that. The readers must not be able to tell the difference, and nothing may be
deleted that has not first been read back and compared.

Two rules the layout itself encodes:

  * **Today's file is never compacted.** `utils.append_records` writes
    `<today>.json` and only that file, so every other day is sealed against the
    collector. Compacting the file being appended to would race the sweep.
  * **If a day has both a `.json` and a `.jsonl.gz`, the `.json` wins.** That
    pair is the crash window between writing the archive and deleting the
    original, and the original is the authoritative copy. Reading both would
    double every record in that day — and a duplicated record in this store is
    not cosmetic: amount matching is the correlator's whole basis, so a
    duplicated exit can be matched twice (see `records_for`'s docstring).

Sealed days still get AMENDED, which is why the archive stays in place rather
than moving to an `archive/` subdirectory: `quarantine_impostor_tokens.py` marks
newly-discovered counterfeit contracts as spam on old records (rule 2, the
$3.07B bug) and `reprice.py` fills in prices it could not resolve at sweep time.
Both must keep working on a compacted day.
"""

import gzip
import json

import pytest

from src.chain import collect

RECORDS = [
    {"id": "ethereum:0xaaa:erc20:0", "src": "0x1111111111111111111111111111111111111111",
     "dst": "0x2222222222222222222222222222222222222222", "asset": "USDC",
     "amount_usd": 1000.0, "spam": False},
    {"id": "ethereum:0xbbb:erc20:0", "src": "0x2222222222222222222222222222222222222222",
     "dst": "0x3333333333333333333333333333333333333333", "asset": "USDC",
     "amount_usd": 250.0, "spam": False},
]

WALLET = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def substrate(tmp_path, monkeypatch):
    """An isolated substrate root, with the module-level path repointed.

    collect.py derives TRANSFERS_DIR from DATA_DIR once at import, so patching
    utils.DATA_DIR alone would write straight through to the real tree — the
    leak tests/conftest.py's "collected transfer records" probe exists for.
    """
    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    collect._FILE_CACHE.clear()
    yield root
    collect._FILE_CACHE.clear()


def _write_json(root, day="2026-09-10", records=RECORDS):
    p = root / "ethereum" / f"{day}.json"
    p.write_text(json.dumps(records))
    return p


def _write_gz(root, day="2026-09-10", records=RECORDS):
    p = root / "ethereum" / f"{day}.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as gz:
        for r in records:
            gz.write(json.dumps(r, separators=(",", ":")) + "\n")
    return p


def test_records_for_reads_a_gzipped_day(substrate):
    """The reader must not be able to tell the two encodings apart."""
    _write_gz(substrate)

    got = collect.records_for(WALLET)

    assert got == RECORDS


def test_records_for_is_identical_across_encodings(substrate, tmp_path, monkeypatch):
    """The equivalence that makes compaction lossless, asserted directly."""
    _write_json(substrate)
    from_json = collect.records_for(WALLET)

    other = tmp_path / "gz_root"
    (other / "ethereum").mkdir(parents=True)
    _write_gz(other)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", other)
    collect._FILE_CACHE.clear()

    assert collect.records_for(WALLET) == from_json


def test_records_by_wallet_reads_a_gzipped_day(substrate):
    """The one-walk sibling reads the same store and must agree."""
    _write_gz(substrate)

    got = collect.records_by_wallet([WALLET])

    assert got[WALLET] == RECORDS


def test_a_day_stored_both_ways_is_read_once_from_the_json(substrate):
    """The crash window between writing the archive and deleting the original.

    Reading both would double every record in that day, and `records_for`
    dedupes on `id` per wallet — so the duplicate would be invisible here and
    fatal in the correlator, which matches on amount.
    """
    _write_json(substrate)
    _write_gz(substrate, records=[dict(r, amount_usd=-1) for r in RECORDS])

    got = collect.records_for(WALLET)

    assert got == RECORDS
    assert [r["amount_usd"] for r in got] == [1000.0, 250.0]


def test_substrate_files_prefers_the_json_of_a_doubled_day(substrate):
    _write_json(substrate)
    _write_gz(substrate)
    _write_gz(substrate, day="2026-09-11")

    names = [p.name for p in collect.substrate_files(substrate / "ethereum")]

    assert names == ["2026-09-10.json", "2026-09-11.jsonl.gz"]


def test_write_records_keeps_a_compacted_day_compacted(substrate):
    """reprice and quarantine amend sealed days; neither may inflate one.

    Writing a `.json` back beside the `.gz` would also create exactly the
    doubled-day state above, on a file nobody is watching.
    """
    p = _write_gz(substrate)
    amended = [dict(r, spam=True) for r in RECORDS]

    collect.write_records(p, amended)

    assert p.exists()
    assert not (substrate / "ethereum" / "2026-09-10.json").exists()
    assert collect.read_records(p) == amended


def test_write_records_leaves_a_plain_day_plain(substrate):
    p = _write_json(substrate)

    collect.write_records(p, RECORDS)

    assert json.loads(p.read_text()) == RECORDS


def test_an_unreadable_archive_reads_as_empty_not_fatal(substrate):
    """Same posture as the JSON path: skipped, never fatal, never cached."""
    p = substrate / "ethereum" / "2026-09-10.jsonl.gz"
    p.write_bytes(b"this is not gzip")

    assert collect.records_for(WALLET) == []


def test_decode_records_raises_so_a_rewriter_can_tell_broken_from_empty(substrate):
    """Rule 5, and here it decides whether a file gets REWRITTEN.

    `reprice.py` says it in its own comment — "a file we cannot read is not a
    file we may rewrite" — and reports it in `files_unreadable`. If a corrupt
    archive read back as `[]` it would look like a day with nothing in it,
    be skipped silently, and drop out of the health count that exists to say
    we are blind. So the rewriters decode through the raising variant.
    """
    p = substrate / "ethereum" / "2026-09-10.jsonl.gz"
    p.write_bytes(b"this is not gzip")

    with pytest.raises((OSError, ValueError, EOFError)):
        collect.decode_records(p)


def test_decode_records_reads_an_empty_archive_as_empty(substrate):
    """An archive with no lines is a real answer, not a failure."""
    p = _write_gz(substrate, records=[])

    assert collect.decode_records(p) == []


# --- the compaction pass itself -------------------------------------------

@pytest.fixture
def compact_env(tmp_path, monkeypatch):
    """compact_data's DATA_DIR and collect's TRANSFERS_DIR pointed at tmp."""
    import scripts.compact_data as cd
    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    monkeypatch.setattr(cd, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    collect._FILE_CACHE.clear()
    return cd, root


def test_compaction_replaces_a_sealed_day_with_its_archive(compact_env):
    cd, root = compact_env
    _write_json(root, day="2026-09-10")

    cd.compact_transfers(dry_run=False)

    assert (root / "ethereum" / "2026-09-10.jsonl.gz").exists()
    assert not (root / "ethereum" / "2026-09-10.json").exists()


def test_compaction_preserves_every_record(compact_env):
    """Lossless is the whole claim; nothing else about this is worth having."""
    cd, root = compact_env
    _write_json(root, day="2026-09-10")

    cd.compact_transfers(dry_run=False)

    archived = collect.read_records(root / "ethereum" / "2026-09-10.jsonl.gz")
    assert archived == RECORDS


def test_compaction_never_touches_todays_file(compact_env):
    """`append_records` writes today's file and only today's.

    Compacting the file a sweep is still appending to would race it, and the
    sweep would then append plain JSON records to a name that no longer exists.
    """
    cd, root = compact_env
    from src.utils import today_str
    today = _write_json(root, day=today_str())

    cd.compact_transfers(dry_run=False)

    assert today.exists()
    assert not (root / "ethereum" / f"{today_str()}.jsonl.gz").exists()


def test_a_dry_run_changes_nothing(compact_env):
    cd, root = compact_env
    p = _write_json(root, day="2026-09-10")
    before = p.read_bytes()

    result = cd.compact_transfers(dry_run=True)

    assert p.read_bytes() == before
    assert not (root / "ethereum" / "2026-09-10.jsonl.gz").exists()
    assert result["files"] == 1


def test_the_original_survives_a_failed_verification(compact_env, monkeypatch):
    """Verify-then-delete, the rule the snapshot archiver already follows.

    The substrate cannot be re-derived from anything else in the repo, so a
    deletion that outran its check is unrecoverable. Record ids are compared,
    not just counted: a count match can hide a mangled record.
    """
    cd, root = compact_env
    p = _write_json(root, day="2026-09-10")

    monkeypatch.setattr(cd, "_verify_archive", lambda *a, **k: False)
    result = cd.compact_transfers(dry_run=False)

    assert p.exists(), "the original was deleted despite verification failing"
    assert json.loads(p.read_text()) == RECORDS
    assert result["failed"] == 1


def test_verification_rejects_an_archive_missing_a_record(compact_env):
    cd, root = compact_env
    archive = _write_gz(root, day="2026-09-10", records=RECORDS[:1])

    assert cd._verify_archive(archive, RECORDS) is False


def test_verification_accepts_a_faithful_archive(compact_env):
    cd, root = compact_env
    archive = _write_gz(root, day="2026-09-10", records=RECORDS)

    assert cd._verify_archive(archive, RECORDS) is True


# --- the graph's own reader ------------------------------------------------

def test_collect_known_edges_reads_a_compacted_substrate(tmp_path, monkeypatch):
    """The reader that rebuilds the ENTIRE graph, and the one most easily missed.

    `collect_known_edges` went through `utils.load_all_records`, which globs
    `*.json` and would therefore have seen a compacted day as absent. Measured
    on the live substrate the moment compaction landed: a chain directory
    holding seven sealed days plus today returned **26,001 records instead of
    ~300,000** — every sealed day silently gone, with no error anywhere.

    That is the exact shape this project keeps paying for: an absence wearing
    the clothes of an answer. The graph would have been rebuilt from one day of
    history and reported itself healthy.
    """
    from src import transfer_graph as tg

    root = tmp_path / "transfers"
    (root / "ethereum").mkdir(parents=True)
    _write_gz(root, day="2026-09-10")
    monkeypatch.setattr(tg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    collect._FILE_CACHE.clear()

    edges = tg.collect_known_edges()

    assert [e["src"] for e in edges] == [r["src"] for r in RECORDS]


def test_substrate_files_ignores_latest_json(substrate):
    """`load_all_records` skipped it; a rollup is not a day of records."""
    _write_gz(substrate, day="2026-09-10")
    (substrate / "ethereum" / "latest.json").write_text(json.dumps({"health": "ok"}))

    names = [p.name for p in collect.substrate_files(substrate / "ethereum")]

    assert names == ["2026-09-10.jsonl.gz"]
