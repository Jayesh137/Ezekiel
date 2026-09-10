# tests/test_gcr_wallets.py
"""The one GCR vector that is flow rather than style.

Style checks compare habits and can only nudge a prior. These addresses either
transact with the target or they do not. The tests below pin the three ways this
could quietly go wrong: matching on shared exchange infrastructure, counting a
failed read as "clean", and mistaking inbound airdrop spam for the address
trading.
"""

from src.gcr_wallets import (
    ACTIONABLE_TIERS,
    build_report,
    check_against_graph,
    check_hyperliquid,
    infrastructure,
    load_addresses,
    watched,
)

DATA = {
    "addresses": {
        "0xAAA": {"tier": "confirmed", "role": "GCR wallet"},
        "0xBBB": {"tier": "linked", "role": "treasury"},
        "0xCCC": {"tier": "exchange_deposit", "role": "Binance deposit"},
        "0xDDD": {"tier": "lead", "role": "weak"},
    },
    "not_gcr": {"0xHOT": "Binance hot wallet, 30M transactions"},
}


def test_only_actionable_tiers_are_watched():
    got = watched(DATA)
    assert set(got) == {"0xaaa", "0xbbb", "0xccc"}
    assert "0xddd" not in got, "a bare lead must not raise alarms"


def test_exchange_deposit_addresses_are_watched():
    """A CEX deposit address belongs to ONE account, so the target sending to
    GCR's deposit address is a direct link — the strongest signal available."""
    assert "exchange_deposit" in ACTIONABLE_TIERS


def test_a_shared_hot_wallet_is_never_a_link():
    """Two Binance hot wallets really are counterparties of both GCR and the
    target. Matching on them would manufacture a link out of infrastructure
    shared by millions of people."""
    assert infrastructure(DATA) == {"0xhot"}
    assert check_against_graph(["0xHOT"], DATA) == []


def test_a_watched_address_in_the_graph_is_reported():
    got = check_against_graph(["0xAAA", "0xirrelevant"], DATA)
    assert [h["address"] for h in got] == ["0xaaa"]
    assert got[0]["tier"] == "confirmed"


def test_graph_matching_is_case_insensitive():
    assert check_against_graph(["0xaaa"], DATA)
    assert check_against_graph(["0xAAA"], DATA)


def test_real_hyperliquid_activity_is_reported():
    states = {"0xaaa": {"read_ok": True, "fills": 12, "account_value": "50000"}}
    got = check_hyperliquid(states, DATA)
    assert got and got[0]["status"] == "ACTIVE"


def test_inbound_airdrop_spam_is_not_activity():
    """0x398d2824... has three unsolicited inbound spotTransfers and one
    automatic dust conversion. It has never traded. Counting that as GCR on
    Hyperliquid would be a false positive of exactly the kind chain/spam.py
    exists to prevent."""
    states = {a: {"read_ok": True, "fills": 0, "ledger": 0,
                  "outbound_ledger": 0, "account_value": "0"}
              for a in watched(DATA)}
    # Three inbound rows, nothing sent, nothing traded, no balance.
    states["0xaaa"] = {"read_ok": True, "fills": 0, "ledger": 3,
                       "outbound_ledger": 0, "account_value": "0"}
    assert check_hyperliquid(states, DATA) == []


def test_a_failed_read_is_unknown_not_clean():
    """The invariant that has already cost this project a false narrative:
    'we could not tell' must never serialise as 'there is nothing there'."""
    states = {"0xaaa": {"read_ok": False}, "0xbbb": {"read_ok": False},
              "0xccc": {"read_ok": False}}
    got = check_hyperliquid(states, DATA)
    assert all(h["status"] == "unknown" for h in got)
    report = build_report(graph_addresses=[], hl_states=states, data=DATA)
    assert report["unreadable"]
    assert "not a clean result" in report["reading"]


def test_a_missing_address_is_unknown_rather_than_absent():
    """With no states supplied at all, every watched address is unknown on BOTH
    counts — Hyperliquid and the bridge — and none of it reads as clean."""
    report = build_report(graph_addresses=[], hl_states={}, data=DATA)
    assert len(report["unreadable"]) == 2 * len(watched(DATA))
    assert "not a clean result" in report["reading"]


def test_a_clean_sweep_says_so_without_claiming_evidence_against():
    """Absence of a connection is expected here — the confirmed wallet went
    quiet in 2022, the treasury in Dec 2024, and the target starts 2026-02-05.
    Reading that as evidence against the hypothesis would be wrong."""
    states = {a: {"read_ok": True, "fills": 0, "ledger": 0,
                  "outbound_ledger": 0, "account_value": "0"}
              for a in watched(DATA)}
    bridges = {a: {"read_ok": True, "touched": False} for a in watched(DATA)}
    report = build_report(graph_addresses=["0xHOT"], hl_states=states,
                          bridge_states=bridges, data=DATA)
    assert report["clean"]
    assert "not evidence against" in report["reading"]


def test_a_hit_reads_as_flow_not_resemblance():
    states = {a: {"read_ok": True, "fills": 0, "ledger": 0,
                  "outbound_ledger": 0, "account_value": "0"}
              for a in watched(DATA)}
    bridges = {a: {"read_ok": True, "touched": False} for a in watched(DATA)}
    report = build_report(graph_addresses=["0xBBB"], hl_states=states,
                          bridge_states=bridges, data=DATA)
    assert not report["clean"]
    assert "flow, not style" in report["reading"]


def test_the_shipped_labels_parse_and_keep_their_provenance():
    """A typo here silently empties the watchlist."""
    data = load_addresses()
    assert data, "data/labels/gcr_addresses.json must load"
    confirmed = [a for a, r in (data["addresses"]).items()
                 if r.get("tier") == "confirmed"]
    assert confirmed == ["0x246ea68f4516f2d09de8754708255d567477ec21"]
    rec = data["addresses"][confirmed[0]]
    # The four independent claims are what make it confirmed; losing any one of
    # them would weaken the identification without anyone noticing.
    joined = " ".join(rec["evidence"])
    assert "15.6558675" in joined
    assert "2,000,000.0 USDC" in joined
    assert "3372" in joined
    assert "TwoDollaHotDoge" in data["provenance"]["claim"]


def test_the_known_infrastructure_list_covers_the_binance_hot_wallets():
    """These two are counterparties of BOTH GCR and the target. If they ever
    drop out of not_gcr they become a false link."""
    infra = infrastructure()
    assert "0x21a31ee1afc51d94c2efccaa2092ad1028285549" in infra
    assert "0x28c6c06298d514db089934071355e5743bf21d60" in infra


def test_unreadable_labels_do_not_crash_the_watch():
    assert watched({}) == {}
    assert check_against_graph(["0xAAA"], {}) == []
    assert build_report(graph_addresses=["0xAAA"], hl_states={}, data={})["clean"]


def test_a_bridge_touch_is_reported():
    """The blind spot this closes: a Hyperliquid deposit credits whatever ACCOUNT
    it names, which need not be the address that funded it. So a GCR wallet could
    open a brand-new HL account and every HL endpoint for that wallet would still
    answer 'nothing here'. The bridge touch is the only place it shows."""
    from src.gcr_wallets import check_bridge
    states = {"0xaaa": {"read_ok": True, "touched": True, "chain": "arbitrum"}}
    got = check_bridge(states, DATA)
    live = [h for h in got if h["status"] == "ACTIVE"]
    assert live and live[0]["address"] == "0xaaa"
    assert live[0]["chain"] == "arbitrum"


def test_no_bridge_touch_is_silent():
    from src.gcr_wallets import check_bridge
    states = {a: {"read_ok": True, "touched": False} for a in watched(DATA)}
    assert check_bridge(states, DATA) == []


def test_an_unreadable_bridge_history_is_unknown_not_clean():
    from src.gcr_wallets import check_bridge
    states = {a: {"read_ok": False} for a in watched(DATA)}
    got = check_bridge(states, DATA)
    assert got and all(h["status"] == "unknown" for h in got)


def test_a_bridge_hit_alone_breaks_the_clean_flag():
    """It must not need a graph or HL hit to raise the alarm."""
    hl = {a: {"read_ok": True, "fills": 0, "ledger": 0, "outbound_ledger": 0,
              "account_value": "0"} for a in watched(DATA)}
    br = {a: {"read_ok": True, "touched": False} for a in watched(DATA)}
    br["0xaaa"] = {"read_ok": True, "touched": True, "chain": "arbitrum"}
    report = build_report(graph_addresses=[], hl_states=hl, bridge_states=br,
                          data=DATA)
    assert report["bridge_hits"]
    assert not report["clean"]
    assert "flow, not style" in report["reading"]


def test_the_bridge_address_is_the_real_one():
    """A typo here silently disarms the tripwire."""
    from src.gcr_wallets import HL_BRIDGE
    from src.utils import load_config
    assert HL_BRIDGE == (load_config().get("hl_bridge_contract") or "").lower()
