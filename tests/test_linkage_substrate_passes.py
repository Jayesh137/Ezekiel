"""The substrate is read ONCE per linkage pass, not once per wallet.

Why this is worth a test of its own. `substrate_linkage` asked
`get_outbound_addresses` for each swept wallet in turn, and that calls
`records_for`, which walks every stored record on every collected chain to
find the ones touching one address. So the cost was O(swept wallets x whole
substrate), and both terms grow on their own: the frontier adds swept wallets,
and `data/transfers/` gains a file a day per chain and is never pruned.

It became a live outage on 2026-09-14. `_load_cached` holds parsed files under
a 256 MiB ceiling; the substrate passed it (394 MB on 2026-09-16), so the LRU
could no longer hold the whole walk and every wallet re-parsed most of it.
Measured on the live substrate: 9.519s per wallet against 0.263s with eviction
disabled — a 36x penalty — putting the phase at 344-472s of the graph step's
600s cap while every other phase together cost ~130s. The step then failed on
its timeout, and a failed step SKIPS the six detection steps behind it
(identities, agents, dormancy, portfolio overlap, roster, accounting).

Raising the cache ceiling is not the fix and this test says so by not
mentioning it: it buys one treadmill step against a substrate that grows daily,
and the ceiling counts FILE bytes while holding parsed objects (394 MB of JSON
measured at 575 MB of heap). Reading the substrate once is what removes the
wallet term altogether.

The assertion is deliberately about SCALING rather than an absolute count.
`target_l1_profile` and `high_fanin_addresses` make their own passes, and a
fixed number of constant passes is fine; what must never return is a pass PER
WALLET. So the same substrate is scored twice, with two swept wallets and with
six, and the file reads must come out equal.
"""

import json

import pytest


def _write_substrate(root, chains=("arbitrum", "ethereum"), days=("2026-09-15", "2026-09-16")):
    """A small substrate shaped like the real one: chain dir / day file."""
    senders = [f"0x{i:040x}" for i in range(1, 9)]
    for chain in chains:
        d = root / chain
        d.mkdir(parents=True, exist_ok=True)
        for day in days:
            rows = [
                {"id": f"{chain}-{day}-{i}", "src": s, "dst": "0xdeposit",
                 "amount_usd": "100.0", "chain": chain}
                for i, s in enumerate(senders)
            ]
            (d / f"{day}.json").write_text(json.dumps(rows))
    return len(chains) * len(days)


def _count_substrate_reads(monkeypatch, tmp_path, swept):
    """Run substrate_linkage over a fixed substrate and count file reads."""
    import src.chain.collect as collect
    import src.chain.labels as labels
    import src.linkage as lk

    root = tmp_path / "transfers"
    files = _write_substrate(root)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", root)
    # A fresh cache per measurement, so one run cannot be scored against
    # another's warm entries.
    monkeypatch.setattr(collect, "_FILE_CACHE", {})

    reads = []
    real_load = collect._load_cached
    monkeypatch.setattr(collect, "_load_cached",
                        lambda path: (reads.append(str(path)), real_load(path))[1])

    # Everything that would reach the network or the real data dir. Each makes
    # a CONSTANT number of passes and is not what this test measures.
    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: set(swept))
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": None, "out_addrs": {"0xdeposit"}})
    monkeypatch.setattr(lk, "load_first_funders", dict)
    monkeypatch.setattr(lk, "high_fanin_addresses", lambda threshold=25: set())
    monkeypatch.setattr(lk, "activity_exclusions",
                        lambda addrs, chains, cache: (set(), []))
    monkeypatch.setattr(lk, "outbound_chains", lambda t: ["arbitrum"])
    monkeypatch.setattr(lk, "activity_cache",
                        lambda max_lookups=0, seconds=0: {})
    monkeypatch.setattr(labels, "load_registry", lambda path: {})
    monkeypatch.setattr(labels, "service_addresses",
                        lambda registry, categories=None: set())

    config = {"excluded_addresses": [], "known_self_wallets": [],
              "known_service_addresses": [], "hl_bridge_contract": "0xbridge",
              "target_wallet": "0xtarget"}
    got = lk.substrate_linkage("0xtarget", list(swept), config=config)
    return len(reads), files, got


def test_substrate_is_not_re_read_for_every_wallet(monkeypatch, tmp_path):
    """Two swept wallets and six must cost the same number of substrate reads.

    Before the fix this was 8 reads against 24 — one full pass per wallet.
    """
    two = [f"0x{i:040x}" for i in range(1, 3)]
    six = [f"0x{i:040x}" for i in range(1, 7)]

    reads_two, files, _ = _count_substrate_reads(monkeypatch, tmp_path, two)
    reads_six, _, _ = _count_substrate_reads(monkeypatch, tmp_path, six)

    assert reads_six == reads_two, (
        f"substrate re-read per wallet: {reads_two} file reads for 2 wallets, "
        f"{reads_six} for 6. The walk must not scale with the wallet count."
    )
    # And the pass itself is a single sweep of the files, not many.
    assert reads_two <= files, (
        f"{reads_two} reads over a {files}-file substrate — more than one pass."
    )


def test_batching_does_not_change_the_evidence(monkeypatch, tmp_path):
    """The point is the cost, not the answer: every swept wallet sharing the
    target's deposit address must still be reported, exactly as before."""
    six = [f"0x{i:040x}" for i in range(1, 7)]
    _reads, _files, got = _count_substrate_reads(monkeypatch, tmp_path, six)

    assert set(got) == set(six), "a wallet with real evidence went missing"
    for w in six:
        assert got[w]["shared_deposit_addresses"] == ["0xdeposit"]
