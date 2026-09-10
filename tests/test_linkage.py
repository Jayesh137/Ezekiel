

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
