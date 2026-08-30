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
from src.chain.client import fetch_kind, probe_activity
from src.utils import DATA_DIR, append_records, load_all_records, save_latest

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


def normalise_row(row: dict, chain: dict, kind: str, price_lookup) -> dict | None:
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
    amount_usd, basis = value_usd(symbol, amount, date_str, price_lookup)

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


def records_for(wallet: str, *, include_spam: bool = False) -> list[dict]:
    """Every stored record touching `wallet`, across every collected chain.

    The single reader. The graph frontier, the tracer and linkage all need
    exactly this, and defining it three times would guarantee three different
    spam-filtering rules — which is how a quarantined forgery ends up alerting
    through one path while being suppressed on another.
    """
    wl = (wallet or "").lower()
    root = Path(TRANSFERS_DIR)
    if not root.exists():
        return []
    out = []
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for rec in load_all_records(str(chain_dir)):
            if rec.get("spam") and not include_spam:
                continue
            if wl in ((rec.get("src") or "").lower(), (rec.get("dst") or "").lower()):
                out.append(rec)
    return out


def _blank_chain_result() -> dict:
    return {"records": 0, "spam": 0, "calls": 0, "cursor": 0, "gaps": [],
            "truncated": False, "error": None, "probed_inactive": False,
            "unpriced": 0, "spam_by_reason": {}, "errors_by_kind": {}}


def sweep_wallet(address: str, chains: list[dict], budget, *, cluster: bool = False,
                 price_lookup=None,
                 dust_usd: float = 1.0, page_size: int = 1000,
                 max_pages: int = 50) -> dict:
    """Collect every transfer for one wallet across `chains`.

    `cluster` wallets (the target and its confirmed wallets) are swept
    unconditionally. Everything else is probed first: one call establishes
    whether an address has ever transacted on a chain, which is far cheaper
    than six full sweeps that return nothing.
    """
    addr = (address or "").lower()
    price_lookup = price_lookup or (lambda symbol, date: None)
    cursors = read_cursors()
    result = {"address": addr, "status": "ok", "chains": {},
              "degraded_sources": []}

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

        if not cluster:
            before = budget.calls_used
            active, probe_error = probe_activity(addr, chain, budget)
            chain_result["calls"] += budget.calls_used - before
            if probe_error:
                # Could not read the chain. Recording this as "inactive" would
                # claim the wallet has nothing here on the strength of a failed
                # request — blindness dressed as knowledge.
                chain_result["error"] = probe_error
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

            for row in walk.rows:
                rec = normalise_row(row, chain, kind, price_lookup)
                if rec is not None:
                    collected.append(rec)

            if walk.last_block > start:
                cursors[key] = walk.last_block
                chain_result["cursor"] = max(chain_result["cursor"], walk.last_block)

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
        if chain_result["error"]:
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
            if chain_result["error"] and name not in degraded:
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
        "degraded_sources": sorted(degraded),
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
