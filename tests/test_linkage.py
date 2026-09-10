

def test_swept_wallets_includes_frontier_sweeps_not_only_config(monkeypatch):
    """The graph frontier sweeps wallets config cannot know about, and those are
    swept just as completely. Measured 2026-09-10: config named 2 while the
    sweep had cursors for 13, so 11 already-swept wallets each cost a live
    Etherscan call out of a free-tier budget."""
    import src.chain.collect as collect
    import src.linkage as lk

    monkeypatch.setattr(collect, "read_cursors", lambda: {
        "arbitrum:0xaaa:erc20": 1,
        "arbitrum:0xaaa:native": 2,
        "ethereum:0xbbb:erc20": 3,
    })
    got = lk.swept_wallets({"target_wallet": "0xTARGET", "known_self_wallets": ["0xSELF"]})
    assert got == {"0xtarget", "0xself", "0xaaa", "0xbbb"}


def test_swept_wallets_ignores_malformed_cursor_keys(monkeypatch):
    import src.chain.collect as collect
    import src.linkage as lk

    monkeypatch.setattr(collect, "read_cursors", lambda: {
        "arbitrum": 1, "": 2, "arbitrum:notanaddress:erc20": 3,
        "arbitrum:0xccc:erc20": 4,
    })
    got = lk.swept_wallets({"target_wallet": "0xTARGET", "known_self_wallets": []})
    assert got == {"0xtarget", "0xccc"}


def test_swept_wallets_survives_an_unreadable_cursor_store(monkeypatch):
    """read_cursors already returns {} on a bad file; this pins that the caller
    still returns the config cluster rather than raising."""
    import src.chain.collect as collect
    import src.linkage as lk

    monkeypatch.setattr(collect, "read_cursors", dict)
    assert lk.swept_wallets({"target_wallet": "0xT", "known_self_wallets": []}) == {"0xt"}


def test_substrate_linkage_skips_wallets_that_were_never_swept(monkeypatch):
    """For an unswept wallet records_for returns only the rows where it happened
    to touch a swept one — a subset that looks like an answer. Judging address
    reuse from that is how this signal produces confident nonsense."""
    import src.linkage as lk

    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: {"0xswept"})
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": None, "out_addrs": {"0xdeposit"}})
    monkeypatch.setattr(lk, "get_outbound_addresses",
                        lambda w, cfg=None, limit=300: {"0xdeposit"})

    got = lk.substrate_linkage("0xtarget", ["0xswept", "0xunswept"],
                               config={"excluded_addresses": [],
                                       "known_self_wallets": []})
    assert set(got) == {"0xswept"}
    assert got["0xswept"]["shared_deposit_addresses"] == ["0xdeposit"]


def test_substrate_linkage_omits_wallets_with_no_evidence(monkeypatch):
    import src.linkage as lk

    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: {"0xswept"})
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": None, "out_addrs": {"0xaaa"}})
    monkeypatch.setattr(lk, "get_outbound_addresses",
                        lambda w, cfg=None, limit=300: {"0xbbb"})

    assert lk.substrate_linkage("0xtarget", ["0xswept"],
                                config={"excluded_addresses": [],
                                        "known_self_wallets": []}) == {}


def test_substrate_linkage_never_claims_a_shared_funder(monkeypatch):
    """Establishing a first funder needs a live lookup this pass does not make.
    Reporting False as a finding would let an offline run contradict the
    scanner's live evidence."""
    import src.linkage as lk

    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: {"0xswept"})
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": "0xfunder", "out_addrs": {"0xdep"}})
    monkeypatch.setattr(lk, "get_outbound_addresses",
                        lambda w, cfg=None, limit=300: {"0xdep"})

    got = lk.substrate_linkage("0xtarget", ["0xswept"],
                               config={"excluded_addresses": [],
                                       "known_self_wallets": []})
    assert got["0xswept"]["shared_funder"] is False
    assert got["0xswept"]["candidate_first_funder"] is None


def test_substrate_linkage_excludes_the_target_itself(monkeypatch):
    import src.linkage as lk

    monkeypatch.setattr(lk, "swept_wallets", lambda cfg: {"0xtarget"})
    monkeypatch.setattr(lk, "target_l1_profile",
                        lambda t: {"first_funder": None, "out_addrs": {"0xdep"}})
    monkeypatch.setattr(lk, "get_outbound_addresses",
                        lambda w, cfg=None, limit=300: {"0xdep"})

    assert lk.substrate_linkage("0xtarget", ["0xtarget"],
                                config={"excluded_addresses": [],
                                        "known_self_wallets": []}) == {}


def test_first_funder_does_not_cap_the_block_range(monkeypatch):
    """endblock was 99999999 while Arbitrum is past 501,000,000, so the search
    covered only the chain's first fifth and any wallet first funded after that
    returned no funder. That is why shared_funder was false for every wallet in
    the graph despite being one of five accepted corroboration vectors."""
    import src.linkage as lk

    seen = []

    def fake_get(params, **kw):
        seen.append(dict(params))
        return {"status": "1", "result": [
            {"from": "0xfunder", "value": "1000000000000000000"}]}

    monkeypatch.setenv("ETHERSCAN_API_KEY", "k")
    monkeypatch.setattr(lk, "etherscan_get", fake_get)

    assert lk.get_first_funder("0xWALLET") == "0xfunder"
    assert seen[0]["endblock"] == "latest"
    assert 99999999 not in seen[0].values()


def test_no_api_key_yields_no_funder_rather_than_a_wrong_one(monkeypatch):
    import src.linkage as lk
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    assert lk.get_first_funder("0xWALLET") is None


def test_a_found_funder_is_cached_permanently(monkeypatch, tmp_path):
    """A first funder is a fact about a transaction that already happened, so
    one lookup per wallet is all this should ever cost."""
    import src.linkage as lk
    cache, spent = lk.resolve_first_funders(
        ["0xAAA", "0xBBB"], cache={}, lookup=lambda w: "0xFUNDER")
    assert cache == {"0xaaa": "0xfunder", "0xbbb": "0xfunder"}
    assert spent == 2


def test_an_unresolved_funder_is_not_cached(monkeypatch):
    """Caching a miss makes a transient outage permanent, and a wallet with no
    inbound history yet may well acquire one later."""
    import src.linkage as lk
    cache, spent = lk.resolve_first_funders(["0xAAA"], cache={}, lookup=lambda w: None)
    assert cache == {}
    assert spent == 1


def test_already_cached_wallets_cost_nothing(monkeypatch):
    import src.linkage as lk
    calls = []
    cache, spent = lk.resolve_first_funders(
        ["0xAAA"], cache={"0xaaa": "0xf"},
        lookup=lambda w: calls.append(w) or "0xf")
    assert spent == 0 and calls == []


def test_the_lookup_budget_is_respected(monkeypatch):
    import src.linkage as lk
    calls = []
    _, spent = lk.resolve_first_funders(
        [f"0x{i:040x}" for i in range(50)], cache={}, max_lookups=4,
        lookup=lambda w: calls.append(w) or "0xf")
    assert spent == 4 and len(calls) == 4


def test_shared_funder_fires_once_the_cache_is_populated(monkeypatch, tmp_path):
    """The whole point of the cache: with a funder known for both sides,
    compute_linkage can finally assert shared_funder."""
    import src.linkage as lk
    link = lk.compute_linkage("0xcand", "0xshared", set(), "0xtarget",
                              "0xshared", set(), excluded=set())
    assert link["shared_funder"] is True
    assert link["linkage_bonus"] > 0


def test_first_funded_by_the_target_is_the_stronger_claim():
    import src.linkage as lk
    direct = lk.compute_linkage("0xcand", "0xtarget", set(), "0xtarget",
                                "0xother", set(), excluded=set())
    shared = lk.compute_linkage("0xcand", "0xshared", set(), "0xtarget",
                                "0xshared", set(), excluded=set())
    assert direct["linkage_bonus"] > shared["linkage_bonus"]
