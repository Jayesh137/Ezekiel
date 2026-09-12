# src/linkage.py
"""L1 wallet-clustering heuristics used to confirm a candidate is the same person.

Two research-backed signals, applied to promising behavioral candidates:

1. Shared funding source (first-funder heuristic). The first funds a fresh wallet
   receives usually come from a CEX withdrawal or a wallet the owner controls. If a
   candidate's first funder is the target itself, or the SAME funder the target was
   first funded by, that is a strong ownership link.

2. Address reuse (highest-confidence heuristic — cryptographic certainty). A CEX
   deposit address is unique to one account. If the candidate sends value to the
   SAME address the target sends to — on any chain, in any asset — they almost
   certainly share a CEX account. Labelled infrastructure (routers, wrapper
   contracts, exchange hot wallets) is excluded first: those receive from
   millions of unrelated people, so a shared one is coincidence, not ownership.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.chain.labels import SERVICE_CATEGORIES
from src.utils import DATA_DIR, etherscan_get, load_config

# Categories that make a shared destination meaningless. Deliberately omits the
# two deposit categories: a CEX deposit address belongs to exactly one exchange
# account, so two wallets sharing one is the strongest ownership evidence there
# is — the very thing this signal looks for. Those are infrastructure for graph
# traversal and evidence here; one category set cannot serve both.
LINKAGE_EXCLUDED_CATEGORIES = SERVICE_CATEGORIES - {"cex_deposit", "cex_deposit_sweep"}


def compute_linkage(candidate: str, candidate_first_funder: str | None,
                    candidate_out_addrs: set, target: str,
                    target_first_funder: str | None, target_out_addrs: set,
                    excluded: set) -> dict:
    """Pure linkage evaluation. Returns evidence, a score bonus, and reasons.

    Separated from Etherscan I/O so the scoring logic is unit-testable.
    """
    candidate = candidate.lower()
    target = target.lower()
    cff = (candidate_first_funder or "").lower()
    tff = (target_first_funder or "").lower()
    excluded = {a.lower() for a in excluded}

    reasons = []
    bonus = 0.0
    shared_funder = False
    shared_deposit = sorted(
        (candidate_out_addrs & target_out_addrs) - excluded - {target, candidate}
    )

    # Shared funding source
    if cff and cff not in excluded:
        if cff == target:
            shared_funder = True
            bonus += 0.15
            reasons.append("First funded directly by the target wallet")
        elif tff and cff == tff and tff not in excluded:
            shared_funder = True
            bonus += 0.12
            reasons.append("Shares the target's original funding source (same CEX/funder)")

    # Address reuse — send to the same (CEX deposit) address
    if shared_deposit:
        bonus += 0.18
        reasons.append(
            f"Sends funds to the same address as target (address reuse): "
            f"{', '.join(a[:10] + '...' for a in shared_deposit[:2])}"
        )

    return {
        "shared_funder": shared_funder,
        "candidate_first_funder": cff or None,
        "shared_deposit_addresses": shared_deposit,
        "linkage_bonus": round(min(bonus, 0.30), 4),
        "reasons": reasons,
    }


def get_first_funder(wallet: str) -> str | None:
    """Earliest external address to send this wallet ETH or USDC on Arbitrum.

    `endblock` is "latest", not a hardcoded ceiling. It was 99999999 while
    Arbitrum is past block 501,000,000, so the search covered only the chain's
    first fifth: any wallet first funded after block 99,999,999 — which is most
    of them — returned no rows and therefore no funder. That is why
    `shared_funder` was false for every wallet in the graph despite being one of
    the five vectors `classify_node` accepts as corroboration.

    Identical in kind to the ceiling that stopped an Arbitrum sweep 220 million
    blocks early (see src/chain/client.py's ENDBLOCK), and worth stating twice:
    a literal block ceiling is a silent walk-stopper on any chain taller than it.
    """
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return None
    wl = wallet.lower()

    # Earliest normal (ETH) inbound tx — funds gas.
    normal = etherscan_get({
        "module": "account", "action": "txlist", "address": wallet,
        "startblock": 0, "endblock": "latest", "page": 1, "offset": 20, "sort": "asc",
    })
    for t in normal.get("result", []) if normal.get("status") == "1" else []:
        frm = (t.get("from", "") or "").lower()
        if frm and frm != wl and int(t.get("value", 0) or 0) > 0:
            return frm

    # Fall back to earliest USDC inbound.
    config = load_config()
    tok = etherscan_get({
        "module": "account", "action": "tokentx", "address": wallet,
        "contractaddress": config["usdc_contract_arbitrum"],
        "page": 1, "offset": 20, "sort": "asc",
    })
    for t in tok.get("result", []) if tok.get("status") == "1" else []:
        to = (t.get("to", "") or "").lower()
        frm = (t.get("from", "") or "").lower()
        if to == wl and frm and frm != wl:
            return frm
    return None


FIRST_FUNDER_PATH = DATA_DIR / "labels" / "first_funders.json"


def load_first_funders() -> dict:
    """Cached wallet -> first funder. Empty on any read failure."""
    try:
        import json
        with open(FIRST_FUNDER_PATH) as f:
            data = json.load(f)
        return {k.lower(): v for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def save_first_funders(funders: dict) -> None:
    import json
    FIRST_FUNDER_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIRST_FUNDER_PATH.write_text(json.dumps(funders, indent=2, sort_keys=True))


def resolve_first_funders(wallets, *, max_lookups: int = 12,
                          cache: dict | None = None,
                          lookup=None) -> tuple[dict, int]:
    """Fill in first funders for wallets missing one. Returns (cache, spent).

    Cached permanently once found, because a wallet's first funder is a fact
    about a transaction that already happened and cannot change. A wallet we
    looked up and found nothing for is NOT cached: that is either a wallet with
    no inbound history yet — which a later run may find — or a failed read, and
    writing it down as "has no funder" would make a transient outage permanent.

    Bounded by `max_lookups` because each miss costs up to two Etherscan calls
    from a free-tier budget shared with the sweep.
    """
    cache = dict(cache if cache is not None else load_first_funders())
    lookup = lookup or get_first_funder
    spent = 0
    for wallet in wallets:
        w = (wallet or "").lower()
        if not w or w in cache:
            continue
        if spent >= max_lookups:
            break
        spent += 1
        funder = lookup(w)
        if funder:
            cache[w] = funder.lower()
    return cache, spent


def swept_wallets(config: dict) -> set:
    """Addresses the substrate holds a real sweep for.

    Config names the cluster swept unconditionally by
    scripts/backfill_transfers.py and by the tracer. But the graph frontier
    sweeps wallets config cannot know about, and those are swept just as
    completely — get_outbound_addresses' own docstring counts them as covered.
    Reading only config therefore under-reported: measured 2026-09-10, config
    named 2 wallets while the sweep had stored cursors for 13.

    The cursors are the sweep's own record of what it has read, so they are the
    authoritative answer to "did we sweep this". Under-reporting here does not
    corrupt a result — get_outbound_addresses unions the substrate with a live
    call — but it spends an Etherscan call per already-swept wallet out of a
    free-tier budget, and it leaves substrate-only callers believing a wallet is
    uncovered when it is not.
    """
    out = {(config.get("target_wallet") or "").lower()}
    out |= {(w or "").lower() for w in config.get("known_self_wallets", [])}

    from src.chain.collect import read_cursors
    for key in read_cursors():
        # "chain:wallet:kind" — the wallet is the one field that is an address.
        parts = str(key).split(":")
        if len(parts) >= 2 and parts[1].startswith("0x"):
            out.add(parts[1].lower())
    return out - {""}


def _live_outbound_usdc(wallet: str, excluded: set, limit: int) -> set:
    """Arbitrum USDC destinations straight from Etherscan. One call.

    The pre-substrate implementation of this whole function, kept for the
    population the substrate does not cover.
    """
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return set()
    config = load_config()
    wl = (wallet or "").lower()
    res = etherscan_get({
        "module": "account", "action": "tokentx", "address": wallet,
        "contractaddress": config["usdc_contract_arbitrum"],
        "page": 1, "offset": limit, "sort": "desc",
    })
    out = set()
    for t in res.get("result", []) if res.get("status") == "1" else []:
        if (t.get("from", "") or "").lower() != wl:
            continue
        to = (t.get("to", "") or "").lower()
        if to and to not in excluded and int(t.get("value", 0) or 0) > 0:
            out.add(to)
    return out


def get_outbound_addresses(wallet: str, config: dict | None = None,
                           limit: int = 300) -> set:
    """Every address this wallet has sent value to, excluding known
    infrastructure.

    For a swept wallet this reads the substrate: src/chain/collect.py has
    already stored these, so widening the strongest linkage signal we have — a
    CEX deposit address belongs to exactly one account, so two wallets funding
    the same one are the same customer — from Arbitrum USDC to every chain and
    asset costs no calls at all.

    For anything else it ALSO makes the one live Etherscan call this function
    used to make. The substrate is populated only for wallets that were swept —
    the target, known_self_wallets and graph-frontier wallets. Leaderboard
    behavioural candidates are a different population and are never swept, so
    records_for() returns only the records where the candidate happened to
    transact with an already-swept wallet: not an empty set that would be
    obviously wrong, but a subset of swept addresses that looks like an answer.
    Reading only the substrate therefore left this signal near-permanently dark
    for exactly the wallets it exists to judge — and it fires
    alert_linkage_match as a standalone alert, not gated behind the score
    threshold.

    Sweeping the candidate instead was the alternative. It is rejected here: a
    sweep writes the candidate's whole history into the shared substrate, which
    feeds collect_known_edges and therefore the transfer graph, so scoring a
    leaderboard wallet would add unrelated wallets to the target's graph; and
    scanner.py has no call budget to bound six chains x three kinds per
    candidate. One call matches what this path already spends per candidate in
    get_first_funder, and matches pre-branch behaviour exactly.

    That signal only holds for a private deposit address. Widening the search
    to every chain and asset also widens the odds of landing on a router, a
    wrapper contract, or an exchange HOT wallet — infrastructure that receives
    from millions of unrelated people, where a shared destination is
    coincidence rather than evidence of common ownership. This result feeds a
    bonus the module calls "cryptographic certainty" and fires a standalone
    alert, so labelled infrastructure is excluded before it ever reaches that
    scoring step.
    """
    from src.chain.collect import records_for
    from src.chain.labels import load_registry, service_addresses

    config = config or load_config()
    wl = (wallet or "").lower()

    excluded = service_addresses(load_registry(DATA_DIR / "labels" / "entities.json"),
                                 categories=LINKAGE_EXCLUDED_CATEGORIES)
    excluded |= {a.lower() for a in config.get("known_service_addresses", [])}
    excluded.add(config["hl_bridge_contract"].lower())
    excluded.add(wl)

    out = set()
    for rec in records_for(wl):
        if (rec.get("src") or "").lower() != wl:
            continue
        usd = rec.get("amount_usd")
        if usd is None or float(usd) <= 0:
            continue
        dst = (rec.get("dst") or "").lower()
        if dst and dst not in excluded:
            out.add(dst)

    # Union, not a fallback-on-empty: a non-empty substrate result is not
    # evidence the wallet was swept, only that it touched something that was.
    if wl not in swept_wallets(config):
        out |= _live_outbound_usdc(wl, excluded, limit)
    return out


def get_outbound_usdc_addresses(wallet: str, limit: int = 300) -> set:
    """Backwards-compatible alias. `limit` caps the live Etherscan page used
    for wallets the substrate does not cover; the substrate itself is complete
    for swept wallets, so there is no page to cap there."""
    return get_outbound_addresses(wallet, limit=limit)


def target_l1_profile(target: str) -> dict:
    """Target's L1 fingerprint for clustering: first funder + outbound addresses.
    Outbound addresses come from the substrate, covering every collected chain
    and asset with no API calls; first funder needs one Etherscan lookup."""
    config = load_config()
    out_addrs = get_outbound_addresses(target, config)

    return {
        "first_funder": get_first_funder(target),
        "out_addrs": out_addrs,
    }


def high_fanin_addresses(threshold: int = 25) -> set:
    """Addresses many unrelated wallets send to, measured from the substrate.

    The address-reuse heuristic rests on a deposit address belonging to exactly
    ONE account, so two wallets funding the same one are the same customer. That
    holds for a private deposit address and collapses for a shared one.

    Measured 2026-09-10: the target and two other wallets shared
    `0xf078969e...f19e`, which has 84 distinct senders and 130 recipients — the
    graph already grades it INFRASTRUCTURE. It was still counted as address
    reuse, because get_outbound_addresses excludes only addresses NAMED in the
    label registry or config, and this one was detected by fan rather than
    named. The same three wallets also shared `0xf076c336...f19e`, which has
    exactly 3 senders and is genuine — so the conclusion survived, by luck.

    Counting distinct senders costs nothing: the substrate is already in memory.
    """
    import collections
    import json as _json

    senders: dict[str, set] = collections.defaultdict(set)
    root = DATA_DIR / "transfers"
    if not root.exists():
        return set()
    for chain_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for path in sorted(chain_dir.glob("*.json")):
            try:
                with open(path) as f:
                    rows = _json.load(f)
            except (OSError, ValueError):
                continue
            for rec in rows if isinstance(rows, list) else []:
                if not isinstance(rec, dict) or rec.get("spam"):
                    continue
                dst = (rec.get("dst") or "").lower()
                src = (rec.get("src") or "").lower()
                if dst and src:
                    senders[dst].add(src)
    return {a for a, s in senders.items() if len(s) >= threshold}


def activity_cache(max_lookups: int = 0, seconds: float | None = None):
    """The global-activity cache, resolved against DATA_DIR at call time.

    `seconds` bounds wall clock as well as calls. A reading is two HTTP
    requests at a 30s timeout, so any caller that actually spends lookups
    should pass one — see chain.activity.ActivityCache.
    """
    from src.chain.activity import ActivityCache
    return ActivityCache(DATA_DIR / "labels" / "address_activity.json",
                         max_lookups=max_lookups, seconds=seconds)


def outbound_chains(wallet: str) -> dict:
    """Destination -> the chain the wallet was last seen sending to it on."""
    from src.chain.collect import records_for
    wl = (wallet or "").lower()
    out: dict[str, str] = {}
    for rec in records_for(wl):
        if (rec.get("src") or "").lower() != wl:
            continue
        dst = (rec.get("dst") or "").lower()
        if dst and rec.get("chain"):
            out[dst] = rec["chain"]
    return out


def activity_exclusions(addresses, chain_of: dict, cache) -> tuple[set, list]:
    """Destinations that cannot be ownership evidence, and the ones not yet measured.

    A shared destination proves common ownership only if it belongs to one
    account. Fan-in inside the substrate cannot establish that: it counts only
    wallets we swept, so SocketGateway (2.19M transactions) showed five senders
    and was counted as a private deposit address shared by the target and his
    treasury. The whole-chain reading from src/chain/activity.py can.

    An address with no reading is excluded too, and reported as pending, so a
    link is delayed rather than asserted on a destination nobody has measured.
    """
    from src.chain.activity import is_busy

    excluded: set = set()
    pending: list = []
    for a in addresses:
        a = (a or "").lower()
        if not a:
            continue
        reading = cache.get(a, chain_of.get(a) or "arbitrum") if cache else None
        busy = is_busy(reading)
        if busy is None:
            excluded.add(a)
            pending.append(a)
        elif busy:
            excluded.add(a)
    return excluded, sorted(pending)


# Readings per run for the target's own destinations. They are the only
# addresses a shared destination can ever be, so the cache converges fast.
ACTIVITY_LOOKUPS_PER_RUN = 20
# ...and a wall-clock bound. One reading is two HTTP requests at a 30s timeout,
# so twenty is up to twenty minutes with no time budget at all — see
# chain.activity.ActivityCache, and transfer_graph's ACTIVITY_SECONDS_PER_RUN
# for the run this actually killed.
ACTIVITY_SECONDS_PER_RUN = 90.0


def substrate_linkage(target: str, wallets, config: dict | None = None) -> dict:
    """Linkage for wallets the substrate already covers. No network calls.

    The graph's linkage evidence came only from `data/candidates/latest.json`,
    which the scanner writes for leaderboard wallets. Graph nodes are a different
    population and were never in it, so `shared_deposit_address` and
    `gas_funded_by_target` — two of the five corroborating vectors
    `classify_node` accepts, worth 0.18 and 0.15 of confidence — could never be
    true for a wallet the graph found itself. Measured 2026-09-10: 0 of 50
    stored candidates carried a linkage block, while the target's own treasury
    shared seven deposit addresses with him.

    Restricted to swept wallets on purpose. For anything else `records_for`
    returns only the records where that wallet happened to transact with an
    already-swept one — a subset that looks like an answer, which is exactly how
    this signal produces confident nonsense. Those still need the live call in
    `get_outbound_addresses`; this pass simply skips them.
    """
    config = config or load_config()
    target = (target or "").lower()
    swept = swept_wallets(config)

    excluded = set(config.get("excluded_addresses", [])) | \
        set(config.get("known_self_wallets", []))
    profile = target_l1_profile(target)
    target_out = profile.get("out_addrs") or set()

    # First funders come from a permanent cache, topped up a few wallets per
    # run. Reading them here is what lets `shared_funder` ever be true for a
    # wallet the graph found itself: the scanner only ever populated
    # leaderboard candidates, and 0 of 50 of those carried a linkage block.
    funders = load_first_funders()
    target_funder = funders.get(target) or profile.get("first_funder")

    # A shared destination is ownership evidence only when the destination
    # belongs to one account. Addresses many unrelated wallets send to are
    # excluded even when nothing has NAMED them as infrastructure.
    excluded = excluded | high_fanin_addresses()

    # And measured against the whole chain, not just our substrate: a router
    # or exchange contract is excluded however few of OUR wallets touched it,
    # and a destination nobody has measured yet is excluded until it is.
    try:
        busy, pending = activity_exclusions(
            target_out, outbound_chains(target),
            activity_cache(max_lookups=ACTIVITY_LOOKUPS_PER_RUN,
                           seconds=ACTIVITY_SECONDS_PER_RUN))
    except Exception as exc:                          # noqa: BLE001
        print(f"[linkage] activity readings unavailable ({type(exc).__name__}: "
              f"{exc}) — every shared destination treated as unmeasured")
        busy, pending = set(target_out), sorted(target_out)
    excluded = excluded | busy
    if pending:
        print(f"[linkage] {len(pending)} of the target's destinations have no "
              f"global-activity reading yet; they cannot count as shared "
              f"deposit addresses until measured")

    out: dict[str, dict] = {}
    for wallet in wallets:
        w = (wallet or "").lower()
        if not w or w == target or w not in swept:
            continue
        link = compute_linkage(
            w,
            funders.get(w),
            get_outbound_addresses(w, config),
            target,
            target_funder,
            target_out,
            excluded,
        )
        if link.get("linkage_bonus", 0) > 0:
            out[w] = link
    return out


def check_candidate(wallet: str, target: str, profile: dict) -> dict:
    """Run both heuristics against one candidate and return linkage evidence."""
    config = load_config()
    excluded = set(config.get("excluded_addresses", [])) | set(config.get("known_self_wallets", []))
    target_out = profile.get("out_addrs", set())
    # Cached readings only: the graph job measures the target's destinations,
    # and an unmeasured destination is no evidence here either.
    try:
        busy, _pending = activity_exclusions(target_out, outbound_chains(target),
                                             activity_cache(max_lookups=0))
    except Exception:                                 # noqa: BLE001
        busy = set(target_out)
    return compute_linkage(
        wallet,
        get_first_funder(wallet),
        get_outbound_usdc_addresses(wallet),
        target,
        profile.get("first_funder"),
        target_out,
        excluded | busy,
    )
