# tests/test_frontier_lookup_budget.py
"""A frontier lookup must be able to afford the chains it is asked to read.

Production, 2026-09-29 to 2026-10-03: no frontier wallet was expanded for four
days, and the same thing had already held discovery dark from 09-16 to 09-27.
Every lookup reported "sweep ok: could not read bsc, monad" and was deferred.

The per-lookup call budget was `len(chains) * 3`, which priced each chain at its
three record kinds and nothing else. A frontier sweep also spends a probe call
per Etherscan chain, and a Blockscout chain spends a completeness check per kind
on top of its pages. Once Base and Optimism moved to Blockscout (09-26/27), five
chains cost the whole 21 calls before BSC was even probed, so BSC and Monad were
budget-exhausted on every wallet. Budget exhaustion is not a plan refusal, so
they were recorded as DEGRADED, and a degraded chain defers the whole wallet.
The wallet was retried next run, failed identically, and the walk never moved.

These tests drive the real expand_frontier -> sweep_wallet -> probe/fetch path
and fake only the two HTTP functions underneath it, so they exercise the call
accounting that failed rather than a model of it.
"""

from src import transfer_graph as tg
from src.chain import blockscout, client
from src.transfer_graph import expand_frontier, normalise_transfer_record

TARGET = "0x45d26f28196d226497130c4bac709d808fed4029"
W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"
USDC = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
NOW = 1_790_000_000

REFUSAL = ("Free API access is not supported for this chain. Please upgrade "
           "your api plan for full chain coverage. https://etherscan.io/apis")

# Production's chain list as of 2026-10-03: five Etherscan chains (BSC refused
# by the free plan) and two keyless Blockscout chains.
PRODUCTION_CHAINS = [
    {"name": "arbitrum", "chain_id": 42161, "native": "ETH", "enabled": True, "priority": 0},
    {"name": "ethereum", "chain_id": 1, "native": "ETH", "enabled": True, "priority": 1},
    {"name": "base", "chain_id": 8453, "native": "ETH", "enabled": True, "priority": 2,
     "reader": "blockscout"},
    {"name": "optimism", "chain_id": 10, "native": "ETH", "enabled": True, "priority": 3,
     "reader": "blockscout"},
    {"name": "polygon", "chain_id": 137, "native": "POL", "enabled": True, "priority": 4},
    {"name": "bsc", "chain_id": 56, "native": "BNB", "enabled": True, "priority": 5},
    {"name": "monad", "chain_id": 143, "native": "MON", "enabled": True, "priority": 6},
]

def _usdc_row(src, dst, usd, block, h):
    return {"blockNumber": str(block), "timeStamp": str(NOW - 86400), "hash": h,
            "logIndex": "0", "from": src, "to": dst, "value": str(int(usd * 1e6)),
            "tokenSymbol": "USDC", "tokenDecimal": "6", "contractAddress": USDC}

def _seed():
    """The target paying W1 $10,000 on Arbitrum: W1 is the one frontier wallet."""
    rec = {"id": "arbitrum:0xseed:erc20:0", "chain": "arbitrum", "chain_id": 42161,
           "block": 100, "ts": NOW - 2 * 86400, "timestamp": None,
           "tx_hash": "0xseed", "src": TARGET, "dst": W1, "kind": "erc20",
           "asset": "USDC", "token_address": USDC, "amount": 10_000.0,
           "amount_usd": 10_000.0, "value_basis": "stable_par",
           "spam": False, "spam_reason": None}
    return [normalise_transfer_record(rec, {TARGET})]

def _fake_network(monkeypatch, chains, *, failing=(), active=None, endless=False):
    """Answer every Etherscan and Blockscout request the sweep makes.

    W1 is active on every chain in `active` (default: all). On Arbitrum it sent
    W2 $5,000 of USDC, so a successful read of W1 adds an edge and queues W2.
    BSC answers with the free plan's refusal. A chain in `failing` answers every
    request with a rate limit, which is a degraded read and not a refusal. With
    `endless`, W1's Arbitrum token history never ends: every page is full.
    """
    by_id = {c["chain_id"]: c["name"] for c in chains}
    active = set(active if active is not None else by_id.values())
    calls = []

    def etherscan_get(params, chain_id=None):
        name = by_id[chain_id]
        calls.append((name, params.get("action"), params.get("offset")))
        if name == "bsc":
            return {"status": "0", "message": "NOTOK", "result": REFUSAL}
        if name in failing:
            return {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
        if name not in active:
            return {"status": "0", "message": "No transactions found", "result": []}
        if params.get("offset") == 1 and params.get("action") == "txlist":
            # The frontier's activity probe: one row means "has transacted here".
            return {"status": "1", "message": "OK",
                    "result": [{"blockNumber": "90", "hash": "0xprobe"}]}
        if name == "arbitrum" and params.get("action") == "tokentx":
            if endless:
                start = int(params.get("startblock") or 0) + 1
                size = int(params["offset"])
                return {"status": "1", "message": "OK", "result": [
                    _usdc_row(W1, W2, 5_000.0, start + i, f"0x{start + i:x}")
                    for i in range(size)]}
            return {"status": "1", "message": "OK",
                    "result": [_usdc_row(W1, W2, 5_000.0, 200, "0xw1w2")]}
        return {"status": "0", "message": "No transactions found", "result": []}

    def blockscout_get(url, params):
        name = next(c["name"] for c in chains if c.get("reader") == "blockscout"
                    and blockscout.HOSTS.get(c["name"]) and url.startswith(blockscout.HOSTS[c["name"]]))
        calls.append((name, url.rsplit("/", 1)[-1], None))
        if name in failing:
            raise RuntimeError("429 Too Many Requests")
        return {"items": [], "next_page_params": None}

    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(client, "etherscan_get", etherscan_get)
    monkeypatch.setattr(blockscout, "_get", blockscout_get)
    config = {**tg.load_config(), "chains": chains}
    monkeypatch.setattr(tg, "load_config", lambda: config)
    return calls

def _walk(budget_overrides=None):
    budget = {**tg.DEFAULTS, "max_expansions": 1, **(budget_overrides or {})}
    return expand_frontier(_seed(), TARGET, budget, now_ts=NOW)

def test_a_wallet_active_on_every_readable_chain_is_expanded_not_deferred(monkeypatch):
    _fake_network(monkeypatch, PRODUCTION_CHAINS)

    edges, diag = _walk()

    assert diag["wallets_expanded"] == [W1], diag["decisions"]
    # BSC is a permanent gap of the free plan, reported as such. Nothing was
    # left unread for want of budget, so nothing is degraded.
    assert diag["unsupported_sources"] == ["bsc"]
    assert diag["degraded_sources"] == []
    assert any(e["src"] == W1 and e["dst"] == W2 for e in edges)

def test_the_budget_follows_the_chain_plan_not_a_fixed_multiple(monkeypatch):
    """Adding readable chains must never silently starve the last of them."""
    wide = PRODUCTION_CHAINS + [
        {"name": f"extra{i}", "chain_id": 900_000 + i, "native": "ETH",
         "enabled": True, "priority": 10 + i} for i in range(4)]
    _fake_network(monkeypatch, wide)

    _edges, diag = _walk()

    assert diag["wallets_expanded"] == [W1], diag["decisions"]
    assert diag["degraded_sources"] == []

def test_a_wallet_with_one_unreadable_chain_still_contributes_what_was_read(monkeypatch):
    """A degraded chain is retried soon; it must not throw away the chains read.

    Before this, one chain failing deferred the whole wallet: the Arbitrum edge
    just read never entered the walk, W2 was never queued, and a chain that kept
    failing kept the wallet (and its recipients) out of the graph indefinitely.
    """
    _fake_network(monkeypatch, PRODUCTION_CHAINS, failing={"monad"})

    edges, diag = _walk()

    assert diag["wallets_expanded"] == [W1], diag["decisions"]
    assert any(e["src"] == W1 and e["dst"] == W2 for e in edges)
    queued = {row["wallet"]: row["depth"] for row in diag["frontier_queue"]}
    assert queued.get(W2) == 2
    # Still reported, never forgiven silently.
    assert "monad" in diag["degraded_sources"]
    assert any(f["wallet"] == W1 and f["chains"] == ["monad"]
               for f in diag["partial_failures"])
    # The unread chain comes back within the hour, not on the three-day cycle
    # a fully-read wallet gets.
    refresh = diag["refresh_state"][W1]
    assert "monad" in refresh["last_error"]
    assert refresh["next_check"] <= NOW + 3600
    assert refresh.get("last_successful_read") is None

def test_a_wallet_whose_every_chain_failed_is_deferred_not_expanded(monkeypatch):
    """Nothing read is not progress: the wallet stays queued for the next run."""
    readable = [c["name"] for c in PRODUCTION_CHAINS if c["name"] != "bsc"]
    _fake_network(monkeypatch, PRODUCTION_CHAINS, failing=set(readable))

    _edges, diag = _walk()

    assert diag["wallets_expanded"] == []
    assert W1 in {row["wallet"] for row in diag["frontier_queue"]}
    assert W1 not in diag["expanded_ledger"]
    assert diag["status"] == "failed"

def test_a_lookup_into_endless_history_still_stops(monkeypatch):
    """Liveness must not be bought by letting one lookup spend without limit.

    A wallet whose history never ends is what an unlabelled exchange looks like
    from the frontier. Etherscan's free tier is 100,000 calls a day shared by
    every job, and the walk makes up to 40 lookups a run.
    """
    calls = _fake_network(monkeypatch, PRODUCTION_CHAINS, endless=True)

    _walk()

    # The full plan for these chains is 29 calls (hand-counted: arbitrum,
    # ethereum, polygon and monad 4 each, bsc 1, base and optimism 6 each).
    # Paging further is fine; paging forever is not.
    assert 29 < len(calls) <= 64
