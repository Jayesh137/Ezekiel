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
from src.utils import DATA_DIR, append_records, save_latest

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
    try:
        records = json.loads(path.read_text())
    except (OSError, ValueError):
        # Same posture as load_all_records: an unreadable file is skipped, not
        # fatal. It is deliberately not cached, so a transient read failure
        # does not stick for the life of the process.
        return []
    if not isinstance(records, list):
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
        for path in sorted(chain_dir.glob("*.json")):
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


def _merge_spam_rollup(entries: list[dict]) -> None:
    """Fold this run's quarantine into the persisted rollup."""
    path = Path(SPAM_DIR) / "latest.json"
    try:
        existing = json.loads(path.read_text()).get("entries", [])
    except (OSError, ValueError):
        existing = []

    by_addr = {e["address"]: e for e in existing if e.get("address")}
    for entry in entries:
        prior = by_addr.get(entry["address"])
        if prior is None:
            by_addr[entry["address"]] = entry
            continue
        prior["count"] += entry["count"]
        prior["first_seen"] = min(prior.get("first_seen", 0) or 0, entry["first_seen"])
        prior["last_seen"] = max(prior.get("last_seen", 0) or 0, entry["last_seen"])
        prior["mimics"] = prior.get("mimics") or entry.get("mimics")

    merged = sorted(by_addr.values(), key=lambda e: -e["count"])
    save_latest(str(SPAM_DIR), {
        "last_updated": datetime.now(UTC).isoformat(),
        "suppressed_total": sum(e["count"] for e in merged),
        "entries": merged,
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
