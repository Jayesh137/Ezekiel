# src/chain/collect.py
"""Sweeping a wallet across chains and writing what we found.

The one entry point is `sweep_wallet`. Everything it depends on is a module
level name so tests can substitute it — the sweep itself is orchestration, and
orchestration is only worth testing if the pieces can be faked.

Two rules this module exists to enforce:

  * A partial sweep persists what it collected. Discarding real history to
    report a failure that is already reported in `degraded_sources` loses data
    for nothing.
  * An empty sweep and a failed sweep never serialise the same way. One says
    "this wallet did nothing"; the other says "we are blind here". Collapsing
    them turns an outage into a silent all-clear.
"""

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from src.chain import spam as spam_mod
from src.chain.assets import decimals_of, value_usd
from src.chain.client import (
    fetch_kind,
    newest_block,
    probe_activity,
    unsupported_for_plan,
)
from src.utils import DATA_DIR, append_records, atomic_write_json, save_latest

TRANSFERS_DIR = DATA_DIR / "transfers"
SPAM_DIR = DATA_DIR / "transfers_spam"
CURSOR_PATH = DATA_DIR / "state" / "transfer_cursors.json"

KINDS = ("erc20", "native", "internal")


def _iso(ts: int) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def read_cursors() -> dict:
    try:
        return json.loads(Path(CURSOR_PATH).read_text())
    except (OSError, ValueError):
        return {}


def write_cursors(cursors: dict) -> None:
    path = Path(CURSOR_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursors, indent=2, sort_keys=True))


def normalise_row(row: dict, chain: dict, kind: str, price_lookup,
                  canonical: dict | None = None) -> dict | None:
    """One raw Etherscan row into the Phase 1 normalised record."""
    src = (row.get("from") or "").lower()
    dst = (row.get("to") or "").lower()
    if not src or not dst or src == dst:
        return None
    try:
        ts = int(row.get("timeStamp", 0) or 0)
        block = int(row.get("blockNumber", 0) or 0)
        raw = int(row.get("value", 0) or 0)
    except (TypeError, ValueError):
        return None

    decimals = decimals_of(row, kind)
    amount = raw / (10 ** decimals)
    symbol = row.get("tokenSymbol") or (chain["native"] if kind != "erc20" else "")
    date_str = (_iso(ts) or "")[:10]
    # The token's own contract, not just the ticker it claims. Anyone can deploy
    # a token called USDC; only one contract IS USDC on a given chain. Stored as
    # `token_address` below, which this file has always recorded and nothing
    # ever checked.
    contract = (row.get("contractAddress") or "").lower() or None
    amount_usd, basis = value_usd(symbol, amount, date_str, price_lookup,
                                  contract=contract, chain=chain["name"],
                                  canonical=canonical)

    index = str(row.get("logIndex") or row.get("traceId") or "0")
    tx_hash = row.get("hash", "")
    return {
        "id": f"{chain['name']}:{tx_hash}:{kind}:{index}",
        "chain": chain["name"],
        "chain_id": chain["chain_id"],
        "block": block,
        "ts": ts,
        "timestamp": _iso(ts),
        "tx_hash": tx_hash,
        "src": src,
        "dst": dst,
        "kind": kind,
        "asset": symbol,
        "token_address": (row.get("contractAddress") or "").lower() or None,
        "amount": amount,
        "amount_usd": amount_usd,
        "value_basis": basis,
        "spam": False,
        "spam_reason": None,
    }


# Parsed stored files, keyed by path. Each entry is
# (mtime_ns, size, records) — the identity of the bytes we parsed, so a file
# rewritten between calls is re-read rather than served stale.
#
# Measured before this existed: records_for cost 0.94s against 51MB / 76,150
# records, of which 0.88s was JSON parsing. expand_frontier calls it once per
# frontier wallet, so a full run spent up to 37s — a quarter of the graph job's
# 150s budget — re-parsing the same bytes. The scanner calls it per candidate
# and the correlator once per run on top.
#
# Files are per chain, per day. Today's is the only one a sweep can touch, so
# after the first call every historical file is a cache hit forever and the
# per-call cost collapses to the filter (~0.06s).
_FILE_CACHE: dict[str, tuple[int, int, list]] = {}

# Ceiling on retained source bytes, evicting least-recently-used. Without it the
# cache grows with data/transfers/ without bound; data/ is already ~142MB and
# only compaction holds it down.
_FILE_CACHE_MAX_BYTES = 256 * 1024 * 1024


# A sealed day is stored gzipped: `<date>.jsonl.gz` beside the `<date>.json`
# of the day still being written. Measured on ethereum/2026-09-10, 113,244
# records: 76.69 MB -> 5.20 MB, 14.8x, read back in 0.25s. The substrate as a
# whole goes 416 MB -> 68.3 MB, which matters because a single daily file was
# within 27 MiB of GitHub's 100 MiB blob limit and a file over it fails the
# push AFTER the run's work is done.
ARCHIVE_SUFFIX = ".jsonl.gz"


def substrate_files(chain_dir: Path) -> list[Path]:
    """One file per stored day, newest encoding rules resolved.

    A day holding BOTH encodings is the crash window between writing the
    archive and deleting the original, and the **original wins**: it is the
    authoritative copy, and reading both would double every record in that day.
    That is not cosmetic here — amount matching is the correlator's whole
    basis, so a duplicated exit is one that can be matched twice, against a
    deposit with no real exit behind it (see `records_for`).
    """
    by_day: dict[str, Path] = {}
    for path in chain_dir.glob("*.json"):
        if path.name == "latest.json":
            continue  # a rollup, not a day of records — load_all_records skips it too
        by_day[path.name[: -len(".json")]] = path
    for path in chain_dir.glob(f"*{ARCHIVE_SUFFIX}"):
        by_day.setdefault(path.name[: -len(ARCHIVE_SUFFIX)], path)
    return [by_day[day] for day in sorted(by_day)]


def decode_records(path: Path) -> list:
    """Records from one stored file in either encoding. RAISES on a bad file.

    The raising variant exists for the rewriters. `reprice.py` puts it plainly
    — "a file we cannot read is not a file we may rewrite" — and reports the
    path in `files_unreadable`. If a corrupt archive decoded to `[]` it would
    be indistinguishable from a day with nothing in it: skipped silently, and
    absent from the health count that exists to say we are blind (rule 5).

    A truncated archive raises EOFError, not OSError, so callers catching by
    type must name it; `gzip.BadGzipFile` is an OSError subclass and needs no
    special case.
    """
    path = Path(path)
    if path.name.endswith(ARCHIVE_SUFFIX):
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            return [json.loads(line) for line in gz if line.strip()]
    return json.loads(path.read_text())


def read_records(path: Path) -> list:
    """Records from one stored file in either encoding, `[]` if unreadable.

    The forgiving variant, for readers walking every day: one corrupt file must
    not take down a walk over all the others. Callers that go on to REWRITE the
    file want `decode_records` instead.
    """
    try:
        records = decode_records(path)
    except (OSError, ValueError, EOFError):
        return []
    return records if isinstance(records, list) else []


def write_records(path: Path, records: list) -> None:
    """Rewrite one stored day, keeping whatever encoding its name implies.

    `reprice.py` and `quarantine_impostor_tokens.py` amend SEALED days — the
    first fills in prices it could not resolve at sweep time, the second marks
    newly-discovered counterfeit contracts as spam on records already stored
    (rule 2, which once booked $3.07B of forgery as real money). Both must keep
    working after a day is compacted, which is why the archive stays in place
    rather than moving to an `archive/` subdirectory.

    Writing a `.json` back beside an existing `.gz` would silently manufacture
    the doubled-day state `substrate_files` exists to resolve, on a file nobody
    is watching — so the encoding is taken from the path, never chosen here.
    """
    path = Path(path)
    if not path.name.endswith(ARCHIVE_SUFFIX):
        atomic_write_json(path, records)
        return
    # Write-then-rename, for the reason atomic_write_json gives: a kill during
    # a plain write leaves a truncated file, and this one is the substrate the
    # whole graph is rebuilt from.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as gz:
            for rec in records:
                gz.write(json.dumps(rec, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_cached(path: Path) -> list:
    """Records from one stored file, parsed at most once per version of it."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    hit = _FILE_CACHE.get(key)
    if hit and hit[0] == stat.st_mtime_ns and hit[1] == stat.st_size:
        _FILE_CACHE[key] = _FILE_CACHE.pop(key)      # mark most-recently-used
        return hit[2]
    # Same posture as load_all_records: an unreadable file is skipped, not
    # fatal, and deliberately not cached, so a transient read failure does not
    # stick for the life of the process. read_records returns [] for both an
    # unreadable file and a genuinely empty one; caching [] for the second is
    # harmless and the stat guard re-reads either way once the bytes change.
    records = read_records(path)
    if not records:
        return []
    _FILE_CACHE[key] = (stat.st_mtime_ns, stat.st_size, records)
    # dict preserves insertion order and a hit re-inserts, so the first key is
    # the least recently used. Never evict the entry just added, even if it
    # alone exceeds the ceiling — returning it having dropped it would make the
    # next call re-parse the same file forever.
    total = sum(entry[1] for entry in _FILE_CACHE.values())
    while total > _FILE_CACHE_MAX_BYTES and len(_FILE_CACHE) > 1:
        oldest = next(iter(_FILE_CACHE))
        total -= _FILE_CACHE.pop(oldest)[1]
    return records


def clear_record_cache() -> None:
    """Drop every parsed file. For tests that rewrite a tree in place."""
    _FILE_CACHE.clear()


def records_for(wallet: str, *, include_spam: bool = False) -> list[dict]:
    """Every stored record touching `wallet`, across every collected chain.

    The single reader. The graph frontier, the tracer and linkage all need
    exactly this, and defining it three times would guarantee three different
    spam-filtering rules — which is how a quarantined forgery ends up alerting
    through one path while being suppressed on another.

    Parsed files are cached by (path, mtime, size) — see _FILE_CACHE. A sweep
    that appends changes today's file's mtime, so the next read of it re-parses
    while every untouched historical file stays a hit.

    Deduplicated by `id` ACROSS files. `append_records` dedupes within the file
    it writes, so a record re-fetched by a later sweep lands in that day's file
    carrying an id the earlier day's file already holds, and nothing downstream
    could tell. Measured on live data: the target held 4 such pairs, one of them
    a $5,999,988 USDC transfer counted twice.

    That is not merely cosmetic double-counting. Amount matching is the entire
    basis of the correlator, so a duplicated exit is an exit that can be matched
    twice — the second match against a deposit that never had a real exit behind
    it.
    """
    wl = (wallet or "").lower()
    root = Path(TRANSFERS_DIR)
    if not root.exists():
        return []
    out = []
    seen: set[str] = set()
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in substrate_files(chain_dir):
            for rec in _load_cached(path):
                if not isinstance(rec, dict):
                    continue
                if rec.get("spam") and not include_spam:
                    continue
                if wl not in ((rec.get("src") or "").lower(),
                              (rec.get("dst") or "").lower()):
                    continue
                rid = rec.get("id")
                if rid:
                    if rid in seen:
                        continue
                    seen.add(rid)
                # A record with no id cannot be judged a duplicate of anything,
                # so it is kept: dropping it would silently lose real history to
                # a missing field.
                out.append(rec)
    return out


def records_by_wallet(wallets, *, include_spam: bool = False) -> dict[str, list[dict]]:
    """`records_for` for many wallets at once, in ONE walk of the substrate.

    Same reader, same spam rule, same per-wallet dedupe by `id` — deliberately
    a sibling of `records_for` rather than a second implementation, for the
    reason that function's docstring gives: three spam rules is how a
    quarantined forgery alerts through one path while being suppressed on
    another.

    It exists because calling `records_for` in a loop is O(wallets x whole
    substrate), and BOTH terms grow on their own: the frontier adds swept
    wallets (61 on 2026-09-12, 208 on 2026-09-16) and `data/transfers/` gains a
    file a day per chain and is never pruned. That is what was failing the
    trace workflow — the phase cost 344-472s of the graph step's 600s cap, and
    a failed step skips the six detection steps behind it.

    The `_FILE_CACHE` ceiling makes it worse rather than saving it: the
    substrate (394 MB) has outgrown the 256 MiB limit, so the LRU cannot hold
    one pass and every wallet re-parses most of the files. Measured per wallet
    on the live substrate: 9.519s against 0.263s with eviction disabled, a 36x
    penalty. Raising that ceiling is not the fix — it buys one step against a
    substrate that grows daily, and it counts FILE bytes while holding parsed
    objects (394 MB of JSON measured at 575 MB of heap). One walk removes the
    wallet term instead: measured 5.5s for all 208 wallets.

    Returns a bucket for every requested wallet, empty ones included: an
    address with no records must read as "nothing stored for it", never as
    absent (rule 5 — a missing answer must not look like a clean one).
    """
    wanted = {(w or "").lower() for w in wallets if w}
    out: dict[str, list[dict]] = {w: [] for w in wanted}
    if not wanted:
        return out
    root = Path(TRANSFERS_DIR)
    if not root.exists():
        return out
    # Per wallet, exactly as records_for scopes its own `seen`: a record is a
    # duplicate only of another record for the SAME wallet.
    seen: dict[str, set[str]] = {w: set() for w in wanted}
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in substrate_files(chain_dir):
            for rec in _load_cached(path):
                if not isinstance(rec, dict):
                    continue
                if rec.get("spam") and not include_spam:
                    continue
                src = (rec.get("src") or "").lower()
                dst = (rec.get("dst") or "").lower()
                # A record can touch two requested wallets; it belongs to both.
                for side in {src, dst} & wanted:
                    rid = rec.get("id")
                    if rid:
                        if rid in seen[side]:
                            continue
                        seen[side].add(rid)
                    out[side].append(rec)
    return out


def _blank_chain_result() -> dict:
    return {"records": 0, "spam": 0, "calls": 0, "cursor": 0, "gaps": [],
            "truncated": False, "error": None, "probed_inactive": False,
            "unpriced": 0, "spam_by_reason": {}, "errors_by_kind": {},
            # Per kind, because the chain-level `cursor` is a summary and a
            # summary is what hid the first live failure: erc20 stopped at
            # block 281,189,292 while native reached 501,442,874, and the
            # single reported number was the 501M.
            "cursor_by_kind": {},
            # A kind whose sweep provably did not reach the newest record on
            # chain. Distinct from `truncated` (we know we stopped early) and
            # from `error` (we could not read at all): this is "we believed we
            # had finished, and we had not".
            "incomplete_kinds": {},
            # We could not check whether this kind finished. Not the same as
            # knowing it did not — see the probe_err branch in sweep_wallet.
            "unverified_kinds": {}}


def unreadability(chain_result: dict) -> str | None:
    """How this chain failed, if it did: "unsupported", "degraded", or None.

    One rule in one place because two callers record it — the per-wallet sweep
    and the run summary built from that sweep — and a summary that contradicts
    the result it was built from is worse than either answer alone.

    "unsupported" is reserved for a chain our API plan does not serve: permanent,
    never retried, and never to be read as an empty chain. Everything else that
    went wrong is "degraded" — blindness we can and must re-read out of.

    Judged across EVERY kind, not `chain_result["error"]`, which holds only the
    LAST kind's error: a rate limit on erc20 followed by a plan refusal on
    internal is still a chain we failed to read. A probe refusal records no
    per-kind errors at all, so it falls back to the chain-level one.
    """
    errors = [e for e in (chain_result.get("errors_by_kind") or {}).values() if e]
    if not errors and chain_result.get("error"):
        errors = [chain_result["error"]]
    if chain_result.get("incomplete_kinds"):
        # Read the wrong amount and did not notice. Never "unsupported".
        return "degraded"
    if not errors:
        return None
    return "unsupported" if all(unsupported_for_plan(e) for e in errors) else "degraded"


def sweep_wallet(address: str, chains: list[dict], budget, *, cluster: bool = False,
                 price_lookup=None, canonical: dict | None = None,
                 dust_usd: float = 1.0, page_size: int = 1000,
                 max_pages: int = 50, plan_refused: dict | None = None) -> dict:
    """Collect every transfer for one wallet across `chains`.

    `canonical` maps (chain, SYMBOL) to the one contract that really is that
    token, so a token merely CALLED "USDC" is not priced as USDC. Injected
    rather than loaded here, like every other dependency in this module: reading
    config inside the sweep would make a test's result depend on the repository
    it runs in. None means "judge nothing", which is the pre-existing behaviour.

    `cluster` wallets (the target and its confirmed wallets) are swept
    unconditionally. Everything else is probed first: one call establishes
    whether an address has ever transacted on a chain, which is far cheaper
    than six full sweeps that return nothing.

    `plan_refused` maps a chain name to the refusal its API returned, and is
    shared across the wallets of ONE run. A plan refusal is a property of the
    API key, not of the address, so asking again per wallet buys nothing and
    costs the scarcest resource the sweep has. Measured 2026-09-12: 90 of 373
    calls in a ten-wallet sweep went to base, optimism and bsc and every one
    came back refused — 24% of the budget, and of the wall clock that actually
    bounds the transfer graph's frontier.

    Deliberately per-run and in-memory, never persisted: each run re-learns the
    refusal from the first wallet that asks, so an upgraded API plan is picked
    up by the next run with nothing to invalidate by hand. The gap is still
    recorded in `unsupported_sources` for every wallet, carrying the API's own
    words — a chain that vanished from the summary would read as "nothing
    there", which is the failure rule 5 exists to prevent.
    """
    addr = (address or "").lower()
    price_lookup = price_lookup or (lambda symbol, date: None)
    cursors = read_cursors()
    result = {"address": addr, "status": "ok", "chains": {},
              # Two different absences, deliberately never merged. `degraded` is
              # blindness we can retry out of; `unsupported` is a chain our API
              # plan does not serve at all, which no retry can fix. Folding the
              # second into the first stalled the transfer graph's frontier for
              # two days: every wallet was deferred over three chains no run
              # would ever read, discarding the three just read successfully.
              "degraded_sources": [], "unsupported_sources": []}

    # Without a key every request returns "Invalid API Key", which would burn
    # the whole budget producing nothing while looking like a rate-limit
    # problem. Named skip instead, matching expand_frontier's existing pattern.
    if not os.environ.get("ETHERSCAN_API_KEY"):
        result["status"] = "skipped_no_api_key"
        for chain in chains:
            blank = _blank_chain_result()
            blank["error"] = "skipped_no_api_key"
            result["chains"][chain["name"]] = blank
            result["degraded_sources"].append(chain["name"])
        print("[collect] ETHERSCAN_API_KEY absent — sweep SKIPPED.")
        return result

    for chain in chains:
        name = chain["name"]
        chain_result = _blank_chain_result()
        result["chains"][name] = chain_result

        # Already settled this run: report it, spend nothing.
        if plan_refused is not None and name in plan_refused:
            chain_result["error"] = plan_refused[name]
            result["unsupported_sources"].append(name)
            continue

        if not cluster:
            before = budget.calls_used
            active, probe_error = probe_activity(addr, chain, budget)
            chain_result["calls"] += budget.calls_used - before
            if probe_error:
                # Could not read the chain. Recording this as "inactive" would
                # claim the wallet has nothing here on the strength of a failed
                # request — blindness dressed as knowledge.
                chain_result["error"] = probe_error
                verdict = unreadability(chain_result)
                if verdict == "unsupported":
                    result["unsupported_sources"].append(name)
                    if plan_refused is not None:
                        plan_refused[name] = probe_error
                else:
                    result["degraded_sources"].append(name)
                continue
            if not active:
                chain_result["probed_inactive"] = True
                continue

        collected: list[dict] = []
        for kind in KINDS:
            key = f"{name}:{addr}:{kind}"
            start = int(cursors.get(key, 0) or 0)
            before = budget.calls_used
            walk, error = fetch_kind(addr, chain, kind, start, budget,
                                     page_size=page_size, max_pages=max_pages)
            chain_result["calls"] += budget.calls_used - before
            chain_result["gaps"].extend(walk.possible_gaps)
            chain_result["truncated"] = chain_result["truncated"] or walk.truncated
            if error:
                chain_result["error"] = error
                chain_result["errors_by_kind"][kind] = error
                # A plan refusal answers for the CHAIN, not for this record
                # kind, so the remaining kinds cannot succeed where this one
                # was refused. Only a plan refusal is final — a rate limit or
                # a timeout is degradation we retry out of, and short-circuiting
                # on those would turn a transient failure into a silent gap.
                if unsupported_for_plan(error):
                    break

            for row in walk.rows:
                rec = normalise_row(row, chain, kind, price_lookup,
                                    canonical=canonical)
                if rec is not None:
                    collected.append(rec)

            if walk.last_block > start:
                cursors[key] = walk.last_block
            reached = max(int(cursors.get(key, 0) or 0), start)
            chain_result["cursor_by_kind"][kind] = reached
            # `cursor` stays the furthest-along kind, as a summary. Taking
            # the minimum instead was tried and is worse: a kind with no
            # records legitimately sits at 0 forever and would drag the whole
            # chain's cursor to 0. The per-kind truth lives in
            # `cursor_by_kind`, and the actual verdict on coverage lives in
            # `incomplete_kinds`, which is evidence-based rather than inferred
            # from comparing cursors.
            chain_result["cursor"] = max(chain_result["cursor"], reached)

            # Completeness is checked, not inferred. `walk_blocks` stops when a
            # page comes back short, which says what the API returned, not what
            # exists — see client.newest_block for the live case where those
            # differed by 220 million blocks and nothing noticed.
            if error is None and cluster:
                newest, probe_err = newest_block(addr, chain, kind, budget)
                chain_result["calls"] += 1
                if probe_err:
                    # Could not check. Recorded, but NOT degradation: a failed
                    # probe says nothing about whether the sweep was complete,
                    # and treating it as failure would flag every chain on any
                    # rate-limited run.
                    chain_result["unverified_kinds"][kind] = probe_err
                elif newest is not None and newest > reached:
                    chain_result["incomplete_kinds"][kind] = (
                        f"stopped at block {reached}, but records exist to "
                        f"{newest}")

        # Volume, not membership: the lookalike rule needs to know which side of
        # a matched pair moved more money. The genuine anchors earn their
        # standing from the records themselves — on the live data the
        # self-wallet and the bridge carry millions, which is exactly why the
        # forgeries of them are detectable.
        volume = spam_mod.counterparty_volume(collected, addr)

        clean, quarantined = [], []
        for rec in collected:
            # `wallet=addr` is load-bearing, not decoration: the swept wallet is
            # absent from `volume` by construction, so without it every record
            # of a wallet that has a funded vanity forgery is convicted of
            # forging its own counterparty and quarantined — the whole sweep
            # lost, permanently, while the run still reports itself healthy.
            reason = spam_mod.classify_spam(rec, volume, wallet=addr,
                                            dust_usd=dust_usd)
            if reason is None:
                clean.append(rec)
                continue
            rec["spam"] = True
            rec["spam_reason"] = reason
            chain_result["spam_by_reason"][reason] = (
                chain_result["spam_by_reason"].get(reason, 0) + 1)
            if reason == "lookalike":
                found = spam_mod.forged_side(rec, volume, wallet=addr,
                                             dust_usd=dust_usd)
                if found:
                    rec["forged"], rec["mimics"] = found
            quarantined.append(rec)

        if clean:
            append_records(str(Path(TRANSFERS_DIR) / name), clean, key_field="id")
        if quarantined:
            _merge_spam_rollup(spam_mod.rollup(quarantined, addr))

        chain_result["records"] = len(clean)
        chain_result["spam"] = len(quarantined)
        chain_result["unpriced"] = sum(
            1 for rec in clean if rec.get("value_basis") == "price_unavailable")
        verdict = unreadability(chain_result)
        if verdict == "unsupported":
            result["unsupported_sources"].append(name)
            if plan_refused is not None:
                plan_refused[name] = chain_result["error"]
        elif verdict == "degraded":
            result["degraded_sources"].append(name)

        # Flushed per chain, immediately after that chain's own writes, rather
        # than once at the end: a run killed mid-sweep (the 10-minute CI
        # timeout budget.py is built around) would otherwise lose cursor
        # progress for chains that had already finished and already written
        # their records. On retry those chains would be re-fetched from
        # scratch, and _merge_spam_rollup's straight-addition merge has no
        # id-based dedup like append_records does — a retried range would
        # inflate `count`/`suppressed_total` by re-adding on top of what was
        # already persisted, unbounded and self-reinforcing.
        write_cursors(cursors)

    return result


# Addresses kept in the quarantine ledger, highest count first. The file is
# rewritten WHOLE on every run and read by nothing — not src/, not scripts/,
# not the dashboard, not data/index.json — and it had reached 38.9 MB across
# 130,099 entries, the largest file in the repo, growing ~5 MB a day: about
# thirteen days from the 100 MiB blob limit that refuses a push after the run
# has already done its work.
#
# 61.5% of those entries had been seen exactly once and the top 1,000 covered
# 54.4% of all suppressions, so the tail was paying for itself in nothing.
# Frequency is also the right thing to keep for the file's one human purpose,
# which spam.rollup states: a legitimate token the registry does not know yet
# should be visible so it can be added to assets.py, and such a token is one
# that keeps turning up.
MAX_SPAM_ENTRIES = 2000


def _merge_spam_rollup(entries: list[dict]) -> None:
    """Fold this run's quarantine into the persisted rollup, capped.

    The aggregates are RUNNING TOTALS, not sums over the stored list. That
    distinction is the whole reason this can be trimmed at all: the previous
    `sum(e["count"] for e in merged)` was correct only while the list was
    complete, so evicting the tail would have silently shrunk the count of what
    we threw away. A ledger of our own blindness that under-reports to save
    space has given up the only thing it is for (rule 5).

    Deliberately NOT published: the number of distinct addresses ever seen.
    Once an entry is evicted a re-sighting is indistinguishable from a first
    sighting, so that figure cannot be maintained exactly — and rule 5 cuts
    both ways, so it is absent rather than approximate and labelled as fact.
    """
    path = Path(SPAM_DIR) / "latest.json"
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    existing = stored.get("entries") or []

    # Migration needs no recount: the legacy `suppressed_total` was a sum over
    # a COMPLETE list, which is exactly the running total to carry forward.
    total = int(stored.get("suppressed_total") or 0)
    by_reason: dict[str, int] = dict(stored.get("by_reason") or {})
    if "by_reason" not in stored:
        for e in existing:
            by_reason[e.get("reason") or "unknown"] = (
                by_reason.get(e.get("reason") or "unknown", 0) + int(e.get("count") or 0))

    by_addr = {e["address"]: e for e in existing if e.get("address")}
    for entry in entries:
        # Counted before the merge, so eviction below can never reach them.
        total += int(entry.get("count") or 0)
        reason = entry.get("reason") or "unknown"
        by_reason[reason] = by_reason.get(reason, 0) + int(entry.get("count") or 0)

        prior = by_addr.get(entry["address"])
        if prior is None:
            by_addr[entry["address"]] = entry
            continue
        prior["count"] += entry["count"]
        prior["first_seen"] = min(prior.get("first_seen", 0) or 0, entry["first_seen"])
        prior["last_seen"] = max(prior.get("last_seen", 0) or 0, entry["last_seen"])
        prior["mimics"] = prior.get("mimics") or entry.get("mimics")

    merged = sorted(by_addr.values(), key=lambda e: -e["count"])
    kept = merged[:MAX_SPAM_ENTRIES]
    save_latest(str(SPAM_DIR), {
        "last_updated": datetime.now(UTC).isoformat(),
        "suppressed_total": total,
        "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        # Said plainly, so the kept list can never be mistaken for the whole —
        # the rule the transfer graph's `edges_truncated` already follows.
        "entries_stored": len(kept),
        "entries_cap": MAX_SPAM_ENTRIES,
        "entries_truncated": len(merged) > len(kept),
        "entries": kept,
    })


def sweep_health(results: list[dict]) -> dict:
    """One summary across every wallet swept this run."""
    records = spam = calls = gaps = unpriced = 0
    degraded: list[str] = []
    unsupported: list[str] = []
    incomplete: dict[str, str] = {}
    spam_by_reason: dict[str, int] = {}
    for res in results:
        for name, chain_result in res["chains"].items():
            records += chain_result["records"]
            spam += chain_result["spam"]
            calls += chain_result["calls"]
            gaps += len(chain_result["gaps"])
            unpriced += chain_result.get("unpriced", 0)
            for reason, count in chain_result.get("spam_by_reason", {}).items():
                spam_by_reason[reason] = spam_by_reason.get(reason, 0) + count
            for kind, why in (chain_result.get("incomplete_kinds") or {}).items():
                incomplete.setdefault(f"{name}:{kind}", why)
            verdict = unreadability(chain_result)
            if verdict == "unsupported" and name not in unsupported:
                unsupported.append(name)
            elif verdict == "degraded" and name not in degraded:
                degraded.append(name)
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "wallets": len(results),
        "records": records,
        "spam_suppressed": spam,
        "unpriced": unpriced,
        "spam_by_reason": spam_by_reason,
        "calls": calls,
        "possible_gaps": gaps,
        # Sweeps that believed they had finished and had not. Kept beside
        # `degraded_sources` rather than folded into it, because "could not
        # read" and "read the wrong amount and did not notice" need different
        # fixes — and the second is the one that hid a $13M trail.
        "incomplete": incomplete,
        "degraded_sources": sorted(degraded),
        # Chains Etherscan's free tier refuses outright. A permanent coverage
        # limit, reported every run so it can never pass for "nothing there".
        "unsupported_sources": sorted(unsupported),
        "per_wallet": results,
    }


def read_sweep_health(directory: str | None = None) -> dict:
    path = Path(directory or TRANSFERS_DIR) / "latest.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def merge_sweep_health(previous: dict | None, results: list[dict]) -> dict:
    """This run's health, keeping per-wallet detail for wallets it did not sweep.

    Two jobs write this file: the trace job sweeps the target every 30 minutes,
    the backfill sweeps the whole cluster on demand. Either writing it blind
    erases the other's record of which chains it could not read — and this file
    is the only place blindness is reported at all.

    Totals and `degraded_sources` describe THIS run only, deliberately. A chain
    the other job could not read last week is not evidence about this run, and
    folding it in would leave an outage showing long after it ended — the
    mirror image of the failure this file exists to prevent.
    """
    health = sweep_health(results)
    swept = {res.get("address") for res in results}
    carried = [res for res in (previous or {}).get("per_wallet") or []
               if res.get("address") not in swept]
    if carried:
        health["per_wallet"] = health["per_wallet"] + carried
        health["carried_over_wallets"] = sorted(
            {a for res in carried if (a := res.get("address"))})
    return health


def save_sweep_health(results: list[dict], directory: str | None = None) -> dict:
    """Write the run's health to `latest.json` without clobbering the other job.

    `directory` defaults to this module's TRANSFERS_DIR, resolved at call time
    so tests that repoint it are honoured.
    """
    target = str(directory or TRANSFERS_DIR)
    health = merge_sweep_health(read_sweep_health(target), results)
    save_latest(target, health)
    return health
