# tests/test_migration_signals.py
"""Tests for the migration-detection upgrades: deposit/withdrawal correlation
(FIFO amount+time matching), L1 clustering linkage, and the unified risk score."""

import pytest

from src import correlator, linkage, risk

DAY = 86400
T = "0x45d26f28196d226497130c4bac709d808fed4029"
W1 = "0x1111111111111111111111111111111111111111"
W2 = "0x2222222222222222222222222222222222222222"


# --- correlator ---------------------------------------------------------------

def test_uniqueness_penalizes_round_numbers():
    assert correlator._uniqueness(1_000_000) < correlator._uniqueness(1_234_567)
    assert correlator._uniqueness(1_234_567) == 1.0
    assert correlator._uniqueness(500_000) > correlator._uniqueness(1_000_000)


def test_correlation_matches_close_amount_soon_after():
    exits = [{"amount": 1_234_567, "ts": 1000, "source": "hl_withdraw", "ref": "0xa"}]
    entries = [{"wallet": W1, "amount": 1_234_000, "ts": 1000 + 2 * DAY}]
    out = correlator.find_correlations(exits, entries, tol_pct=0.03, window_days=14,
                                       min_amount=100_000, min_confidence=0.55)
    assert len(out) == 1
    assert out[0]["wallet"] == W1
    assert out[0]["confidence"] >= 0.85  # near-exact odd amount, quick re-entry


def test_correlation_rejects_out_of_window_and_out_of_tolerance():
    exits = [{"amount": 1_000_000, "ts": 1000}]
    # too late
    late = correlator.find_correlations(
        exits, [{"wallet": W1, "amount": 1_000_000, "ts": 1000 + 30 * DAY}],
        window_days=14, min_amount=100_000)
    assert late == []
    # amount too far off
    far = correlator.find_correlations(
        exits, [{"wallet": W1, "amount": 1_200_000, "ts": 1000 + DAY}],
        tol_pct=0.03, min_amount=100_000)
    assert far == []


def test_correlation_fifo_consumes_exit_once():
    exits = [{"amount": 1_234_567, "ts": 1000}]
    entries = [
        {"wallet": W1, "amount": 1_234_567, "ts": 1000 + DAY},
        {"wallet": W2, "amount": 1_234_567, "ts": 1000 + 2 * DAY},
    ]
    out = correlator.find_correlations(exits, entries, min_amount=100_000, min_confidence=0.5)
    assert len(out) == 1  # single exit can only re-link to one deposit


def test_correlation_ignores_below_min_amount():
    out = correlator.find_correlations(
        [{"amount": 5000, "ts": 1000}],
        [{"wallet": W1, "amount": 5000, "ts": 1000 + DAY}],
        min_amount=100_000)
    assert out == []


# collect_target_exits: the L1 side now reads the substrate (records_for),
# not the frozen pre-substrate data/l1_transactions table — nothing writes
# that table any more since src/tracer.py was pointed at the substrate.

def test_collect_target_exits_includes_outbound_substrate_transfer_above_min(
        tmp_path, monkeypatch):
    import json

    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": T, "dst": "0xcex",
         "tx_hash": "0xexit", "ts": 1000, "amount_usd": 250_000.0,
         "value_basis": "stable_par", "spam": False},
    ]))

    exits = correlator.collect_target_exits(T, min_amount=100_000)
    assert exits == [
        {"amount": 250_000.0, "ts": 1000, "source": "l1_outbound", "ref": "0xexit"},
    ]


def test_collect_target_exits_excludes_inbound_substrate_transfer(tmp_path, monkeypatch):
    import json

    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xfunder", "dst": T,
         "tx_hash": "0xin", "ts": 1000, "amount_usd": 250_000.0,
         "value_basis": "stable_par", "spam": False},
    ]))

    assert correlator.collect_target_exits(T, min_amount=100_000) == []


def test_collect_target_exits_skips_price_unavailable_rather_than_treating_as_zero(
        tmp_path, monkeypatch):
    """amount_usd is None when value_basis is price_unavailable — a known asset
    (e.g. ETH) whose price we could not fetch this run. Amount-matching is the
    entire basis of correlation, so an exit of unknown size must be excluded
    outright: it must not raise (None reaching a numeric comparison), and it
    must not silently become a $0 exit either, which — for a min_amount of
    exactly 0 — `find_correlations` would not reject the way it rejects every
    other below-minimum exit."""
    import json

    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": T, "dst": "0xcex",
         "tx_hash": "0xunpriced", "ts": 1000, "amount_usd": None, "asset": "ETH",
         "value_basis": "price_unavailable", "spam": False},
    ]))

    assert correlator.collect_target_exits(T, min_amount=0) == []


def test_collect_target_exits_still_includes_hl_withdrawals(tmp_path, monkeypatch):
    """The ledger branch above the substrate rewrite must be unaffected."""
    import json

    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    (ledger_dir / "2026-08-28.json").write_text(json.dumps([
        {"delta": {"type": "withdraw", "usdc": "300000"}, "time": 1_700_000_000_000,
         "hash": "0xwithdraw"},
    ]))

    exits = correlator.collect_target_exits(T, min_amount=100_000)
    assert exits == [
        {"amount": 300_000.0, "ts": 1_700_000_000, "source": "hl_withdraw",
         "ref": "0xwithdraw"},
    ]


def test_collect_target_exits_skips_malformed_amount_usd_rather_than_raising(
        tmp_path, monkeypatch):
    """amount_usd is only ever produced internally, but a hand-edited or
    truncated file in data/transfers/ should skip one bad record, not crash
    the correlator. min_amount=0 so the ordinary amount gate can't coincide
    with a skip and mask a real bug."""
    import json

    from src.chain import collect

    monkeypatch.setattr(correlator, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": T, "dst": "0xcex",
         "tx_hash": "0xbad", "ts": 1000, "amount_usd": "not-a-number",
         "value_basis": "stable_par", "spam": False},
    ]))

    assert correlator.collect_target_exits(T, min_amount=0) == []


# --- linkage ------------------------------------------------------------------

def test_linkage_direct_funding_by_target():
    out = linkage.compute_linkage(W1, T, set(), T, None, set(), excluded=set())
    assert out["shared_funder"] is True
    assert out["linkage_bonus"] >= 0.15


def test_linkage_shared_deposit_address_is_strongest():
    cex = "0xdeadbeef00000000000000000000000000000000"
    out = linkage.compute_linkage(W1, None, {cex}, T, None, {cex}, excluded=set())
    assert out["shared_deposit_addresses"] == [cex]
    assert out["linkage_bonus"] >= 0.18


def test_linkage_excluded_addresses_ignored():
    cex = "0xdeadbeef00000000000000000000000000000000"
    out = linkage.compute_linkage(W1, cex, {cex}, T, cex, {cex}, excluded={cex})
    assert out["shared_funder"] is False
    assert out["shared_deposit_addresses"] == []
    assert out["linkage_bonus"] == 0.0


def test_linkage_bonus_capped():
    cex = "0xcexdeposit000000000000000000000000000000"
    out = linkage.compute_linkage(W1, T, {cex}, T, None, {cex}, excluded=set())
    assert out["linkage_bonus"] <= 0.30


# get_outbound_addresses: the address-reuse signal's own source, feeding compute_linkage.

def _swept(monkeypatch, *wallets):
    """Declare these wallets as ones the substrate is complete for.

    get_outbound_addresses ALSO makes the one live Etherscan call it used to
    make for a wallet that was never swept, because for those records_for() is
    at best a subset of already-swept addresses rather than the wallet's real
    outbound set. Every test below pre-plants the substrate, so every one of
    them is testing the swept population and has to say so - otherwise they
    pass only because no ETHERSCAN_API_KEY happens to be exported, and reach
    the network on a machine where one is.
    """
    monkeypatch.setattr(linkage, "swept_wallets",
                        lambda config: {(w or "").lower() for w in wallets})

def test_outbound_addresses_come_from_every_chain_without_api_calls(tmp_path, monkeypatch):
    """Address reuse is the strongest linkage signal available, and it was
    limited to Arbitrum USDC. The substrate already holds every chain, so
    widening it costs nothing."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: pytest.fail("must not call the API"))

    for chain, dst in (("arbitrum", "0xdeposita"), ("base", "0xdepositb")):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([{
            "id": f"{chain}:0xh:erc20:0", "chain": chain, "src": "0xtarget",
            "dst": dst, "amount_usd": 500000.0, "ts": 1781000000,
            "spam": False, "value_basis": "stable_par", "asset": "USDC"}]))

    got = linkage.get_outbound_addresses("0xtarget")
    assert got == {"0xdeposita", "0xdepositb"}


def test_outbound_addresses_exclude_spam_and_the_bridge(tmp_path, monkeypatch):
    import json

    from src.chain import collect
    from src.utils import load_config

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    bridge = load_config()["hl_bridge_contract"].lower()

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xtarget", "dst": bridge,
         "amount_usd": 1.0, "ts": 1, "spam": False},
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xpoison",
         "amount_usd": 0.0, "ts": 2, "spam": True, "spam_reason": "lookalike"},
        {"id": "c", "chain": "arbitrum", "src": "0xtarget", "dst": "0xreal",
         "amount_usd": 900.0, "ts": 3, "spam": False},
        {"id": "d", "chain": "arbitrum", "src": "0xstranger", "dst": "0xtarget",
         "amount_usd": 900.0, "ts": 4, "spam": False},
    ]))

    assert linkage.get_outbound_addresses("0xtarget") == {"0xreal"}


def test_outbound_addresses_exclude_labelled_infrastructure(tmp_path, monkeypatch):
    """A shared destination is only evidence of common ownership when it could
    be a private deposit address. A CEX hot wallet receives from millions of
    unrelated people, so an overlap there is coincidence — and this result
    feeds a standalone alert, so a coincidence must never reach the user as a
    confident ownership claim."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")

    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "entities.json").write_text(json.dumps({"entities": [
        {"address": "0xhotwallet", "chain": "arbitrum", "entity": "Binance 8",
         "category": "cex_hot", "source": "public label", "added": "2026-08-28"},
    ]}))

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xtarget", "dst": "0xhotwallet",
         "amount_usd": 900.0, "ts": 1, "spam": False},
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xreal",
         "amount_usd": 900.0, "ts": 2, "spam": False},
    ]))

    assert linkage.get_outbound_addresses("0xtarget") == {"0xreal"}


def test_outbound_addresses_do_not_exclude_cex_deposit_labels(tmp_path, monkeypatch):
    """Regression guard: a curated cex_deposit (or cex_deposit_sweep) label must
    NOT be excluded here. service_addresses() was built to answer "may the
    graph walk into this address?", where a deposit address correctly answers
    no. Linkage asks a different question — "does shared use of this address
    imply common ownership?" — and for that question a deposit address is the
    strongest possible yes: it belongs to exactly one exchange account. That is
    the entire signal this function exists to find; excluding it would invert
    it silently the day someone curates a confirmed deposit address."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")

    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "entities.json").write_text(json.dumps({"entities": [
        {"address": "0xcexdeposit", "chain": "arbitrum", "entity": "Binance deposit",
         "category": "cex_deposit", "source": "curated", "added": "2026-08-28"},
    ]}))

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xtarget", "dst": "0xcexdeposit",
         "amount_usd": 900.0, "ts": 1, "spam": False},
    ]))

    assert linkage.get_outbound_addresses("0xtarget") == {"0xcexdeposit"}


def test_outbound_addresses_exclude_configured_service_addresses(tmp_path, monkeypatch):
    """`known_service_addresses` in config.json is the other place infrastructure
    can be named, alongside the curated label registry."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": "0xtarget", "dst": "0xrouter",
         "amount_usd": 900.0, "ts": 1, "spam": False},
        {"id": "b", "chain": "arbitrum", "src": "0xtarget", "dst": "0xreal",
         "amount_usd": 900.0, "ts": 2, "spam": False},
    ]))

    config = {"hl_bridge_contract": "0xbridge", "known_service_addresses": ["0xrouter"]}
    assert linkage.get_outbound_addresses("0xtarget", config) == {"0xreal"}


# --- the address-reuse signal must not be dark for unswept candidates ----------
#
# The substrate is populated only for wallets that were swept: the target,
# known_self_wallets and graph-frontier wallets. Leaderboard behavioural
# candidates are a different population and are never swept, so reading only
# records_for() left this signal near-permanently empty for exactly the wallets
# it exists to judge - and it fires alert_linkage_match as a standalone alert,
# not gated behind the score threshold.

CANDIDATE = "0xcandidate"


def _live_rows(*destinations, frm=CANDIDATE):
    return {"status": "1", "result": [
        {"from": frm, "to": d, "value": "900000000"} for d in destinations]}


def test_an_unswept_candidate_still_yields_its_outbound_addresses(tmp_path, monkeypatch):
    """The required behaviour: a leaderboard candidate nobody swept must still
    produce its real deposit destinations."""
    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")          # the candidate is NOT in it
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: _live_rows("0xdeposita", "0xdepositb"))

    assert linkage.get_outbound_addresses(CANDIDATE) == {"0xdeposita", "0xdepositb"}
    # and through the alias the scanner actually calls
    assert linkage.get_outbound_usdc_addresses(CANDIDATE) == {"0xdeposita", "0xdepositb"}


def test_an_unswept_candidates_partial_substrate_is_unioned_not_trusted(
        tmp_path, monkeypatch):
    """For an unswept candidate records_for() returns only the records where it
    transacted with an ALREADY-SWEPT wallet - a subset of swept addresses that
    looks like an answer. A non-empty result is therefore not evidence the
    wallet was swept, so the live lookup is unioned in rather than used only as
    a fallback-on-empty."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: _live_rows("0xrealdeposit"))

    d = tmp_path / "transfers" / "arbitrum"
    d.mkdir(parents=True)
    (d / "2026-08-28.json").write_text(json.dumps([
        {"id": "a", "chain": "arbitrum", "src": CANDIDATE, "dst": "0xtarget",
         "amount_usd": 900.0, "ts": 1, "spam": False}]))

    assert linkage.get_outbound_addresses(CANDIDATE) == {"0xtarget", "0xrealdeposit"}


def test_a_swept_wallet_still_costs_no_api_call(tmp_path, monkeypatch):
    """The branch's gain is preserved where the substrate is actually complete:
    the target's outbound set spans every chain and asset, for free."""
    import json

    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: pytest.fail("a swept wallet needs no API call"))

    for chain, dst in (("arbitrum", "0xdeposita"), ("base", "0xdepositb")):
        d = tmp_path / "transfers" / chain
        d.mkdir(parents=True)
        (d / "2026-08-28.json").write_text(json.dumps([{
            "id": f"{chain}:0xh:erc20:0", "chain": chain, "src": "0xtarget",
            "dst": dst, "amount_usd": 500000.0, "ts": 1, "spam": False}]))

    assert linkage.get_outbound_addresses("0xtarget") == {"0xdeposita", "0xdepositb"}


def test_the_live_lookup_excludes_infrastructure_exactly_like_the_substrate_path(
        tmp_path, monkeypatch):
    """The live half feeds the same "cryptographic certainty" bonus and the same
    standalone alert, so it cannot have a weaker exclusion rule."""
    from src.chain import collect
    from src.utils import load_config

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    monkeypatch.setenv("ETHERSCAN_API_KEY", "test-key-not-a-secret")
    bridge = load_config()["hl_bridge_contract"].lower()
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: _live_rows(bridge, CANDIDATE, "0xreal"))

    assert linkage.get_outbound_addresses(CANDIDATE) == {"0xreal"}


def test_without_a_key_the_live_half_degrades_to_empty_rather_than_erroring(
        tmp_path, monkeypatch):
    from src.chain import collect

    monkeypatch.setattr(linkage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
    _swept(monkeypatch, "0xtarget")
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setattr(linkage, "etherscan_get",
                        lambda *a, **k: pytest.fail("must not call the API without a key"))

    assert linkage.get_outbound_addresses(CANDIDATE) == set()


# --- risk score ---------------------------------------------------------------

def test_risk_low_when_quiet():
    r = risk.compute_risk_score({})
    assert r["score"] == 0.0
    assert r["level"] == "LOW"


def test_risk_critical_when_everything_fires():
    r = risk.compute_risk_score({
        "days_silent": 12, "drawdown_pct": 0.6, "l1_outbound": True,
        "hl_native_outbound": True, "top_candidate_score": 0.95,
        "correlation_confidence": 0.9, "linkage_hit": True, "xyz_abandoned": True,
        "top_candidate_wallet": W1,
    })
    assert r["score"] >= 75
    assert r["level"] == "CRITICAL"
    assert r["top_candidate_wallet"] == W1


def test_risk_top_candidate_below_threshold_scores_zero_there():
    """A candidate below the behavioural gate contributes nothing.

    The bar is derived from the thresholds rather than hardcoded. compute_risk_score
    falls back to reading profile/backtest.json when no thresholds are passed, so a
    literal score is a hostage to whatever ceiling was last measured: this test used
    0.60 and broke the moment a real backtest landed a ceiling of 0.5365, which put
    the gate at 0.4665 and made 0.60 a legitimately scoring candidate.
    """
    from src import thresholds as th
    eff = th.resolve(
        {"similarity_high": 0.90, "similarity_medium": 0.80, "similarity_low": 0.65},
        {"passed": True, "self_score": 0.75, "scoring_schema": th.SCORING_SCHEMA})
    below_gate = th.behavioural_gate(eff) - 0.01

    r = risk.compute_risk_score({"top_candidate_score": below_gate}, eff)
    assert all(f["signal"] != "top_candidate" for f in r["factors"])

    # and just above it, the signal does appear — otherwise this proves nothing
    r2 = risk.compute_risk_score({"top_candidate_score": th.behavioural_gate(eff) + 0.01}, eff)
    assert any(f["signal"] == "top_candidate" for f in r2["factors"])


def test_risk_levels_monotonic():
    mild = risk.compute_risk_score({"days_silent": 3})["score"]
    strong = risk.compute_risk_score({"days_silent": 3, "top_candidate_score": 0.9,
                                      "correlation_confidence": 0.8})["score"]
    assert strong > mild


def test_xyz_abandoned_detection():
    from src.utils import now_ms
    old = now_ms() - 20 * 86_400_000
    recent = now_ms() - 1 * 86_400_000
    assert risk._xyz_abandoned([{"coin": "xyz:SP500", "time": old}]) is True
    assert risk._xyz_abandoned([{"coin": "xyz:SP500", "time": recent}]) is False
    assert risk._xyz_abandoned([{"coin": "BTC", "time": old}]) is False  # never traded xyz


# --- amount rarity, measured against the real candidate pool ------------------
#
# The correlator is the mechanism most likely to actually find a migrated
# wallet: he exits ~$X to an exchange and a fresh wallet re-enters ~$X. Whether
# that match is evidence depends entirely on how many OTHER deposits would have
# matched just as well — which only became answerable once the candidate pool
# stopped being truncated to the most recent page.

def _pool(*amounts):
    return [{"wallet": f"0xw{i}", "amount": a, "ts": 1000} for i, a in enumerate(amounts)]


def test_a_unique_amount_in_a_real_pool_scores_maximum_rarity():
    from src import correlator
    population = [1_000_000.0] * 50 + [7_253_701.06]

    rarity, competitors = correlator._rarity(7_253_701.06, population, 0.03)

    assert rarity == 1.0
    assert competitors == 0


def test_a_crowded_amount_scores_low_however_odd_it_looks():
    """The round-number proxy would call 7,253,701.06 maximally distinctive.
    Against a pool where fifty deposits sit within tolerance of it, it is not."""
    from src import correlator
    population = [7_253_701.06] * 50

    rarity, competitors = correlator._rarity(7_253_701.06, population, 0.03)

    assert rarity < 0.05
    assert competitors == 49
    assert correlator._uniqueness(7_253_701.06) == 1.0, (
        "the shape proxy still calls it unique — which is the point")


def test_rarity_counts_near_matches_not_only_exact_ones():
    """A deposit 1% away would have matched the same exit, so it competes."""
    from src import correlator
    population = [1_000_000.0, 1_005_000.0, 1_009_000.0, 5_000_000.0]
    population += [9_000_000.0 + i for i in range(30)]   # push past the floor

    rarity, competitors = correlator._rarity(1_000_000.0, population, 0.03)

    assert competitors == 2, "the two amounts within 3% should compete"
    assert rarity == pytest.approx(1 / 3, abs=0.01)


def test_a_small_pool_falls_back_to_the_shape_proxy():
    """With a handful of candidates, 'unique' is an artefact of the sample."""
    from src import correlator
    tiny = [1_234_567.0, 2_000_000.0]

    rarity, competitors = correlator._rarity(1_000_000.0, tiny, 0.03)

    assert rarity == correlator._uniqueness(1_000_000.0) == 0.30
    assert competitors == 0


def test_a_crowded_match_is_scored_below_a_unique_one_end_to_end():
    from src import correlator
    exits = [{"amount": 1_000_000.0, "ts": 1_000_000, "source": "hl_withdraw", "ref": "x"}]
    crowded = _pool(*([1_000_000.0] * 40))
    for e in crowded:
        e["ts"] = 1_100_000

    unique_pool = _pool(*([3_000_000.0 + i * 100_000 for i in range(39)] + [1_000_000.0]))
    for e in unique_pool:
        e["ts"] = 1_100_000

    crowded_hits = correlator.find_correlations(exits, crowded, min_confidence=0.0)
    unique_hits = correlator.find_correlations(
        [dict(exits[0])], unique_pool, min_confidence=0.0)

    assert crowded_hits and unique_hits
    assert unique_hits[0]["confidence"] > crowded_hits[0]["confidence"], (
        "a match nothing else could explain must outrank one forty deposits share")
    assert crowded_hits[0]["competing_deposits"] == 39
    assert unique_hits[0]["competing_deposits"] == 0


def test_an_incomplete_candidate_pool_is_reported_not_silently_scored(monkeypatch):
    """A run that could not see the whole pool has not cleared the target — it
    has not looked. match_count 0 must not read as 'no migration'."""
    from src import correlator
    monkeypatch.setattr(correlator, "collect_target_exits", lambda t, m: [])
    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda w, m: ([], "hit the page ceiling before the window ended"))
    monkeypatch.setattr(correlator, "save_latest", lambda d, data: "")

    result = correlator.run_correlation()

    assert result["match_count"] == 0
    assert result["candidate_pool_error"], (
        "a zero match count with a truncated pool must say the pool was truncated")


def test_a_whole_candidate_pool_records_no_error(monkeypatch):
    from src import correlator
    monkeypatch.setattr(correlator, "collect_target_exits", lambda t, m: [])
    monkeypatch.setattr(correlator, "get_recent_bridge_deposits",
                        lambda w, m: ([{"wallet": "0xa", "amount": 1e6, "ts": 1}], None))
    monkeypatch.setattr(correlator, "save_latest", lambda d, data: "")

    result = correlator.run_correlation()

    assert result["candidate_pool_error"] is None
    assert result["candidates_considered"] == 1
