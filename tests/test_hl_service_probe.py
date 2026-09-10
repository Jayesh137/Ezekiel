# tests/test_hl_service_probe.py
"""Recognising HL-native infrastructure from a wallet's own ledger.

transfer_graph.detect_services counts degrees among edges already collected, so
an address only looks like a service after it has been swept. An exchange the
target used stays an ordinary counterparty until then.

Live case this was built from: 0x6b9e7731... sent the target ~$45M over five
weeks and graded OPERATIONAL_COUNTERPARTY at 35%, because our edge set held its
transfers with the target and nothing else. Its own ledger shows 583 distinct
destinations, $31 of equity and no trades — infrastructure, one free call away.
"""

import src.ledger_analyzer as la

WALLET = "0x" + "aa" * 20


def _send(src, dst, usd="1000"):
    return {"time": 1_760_000_000_000,
            "delta": {"type": "send", "user": src, "destination": dst,
                      "usdcValue": usd, "token": "USDC"}}


def _peer(n):
    return "0x" + f"{n:040x}"


def test_spread_counts_distinct_wallets_each_way():
    ledger = [_send(WALLET, _peer(1)), _send(WALLET, _peer(2)),
              _send(WALLET, _peer(1)),            # repeat is one distinct peer
              _send(_peer(3), WALLET)]
    assert la.counterparty_spread(ledger, WALLET) == (2, 1)


def test_spread_ignores_unrelated_transfers():
    """A ledger can contain rows the wallet is not party to; they say nothing
    about its own fan degree."""
    assert la.counterparty_spread([_send(_peer(1), _peer(2))], WALLET) == (0, 0)


def test_spread_ignores_non_counterparty_delta_types():
    ledger = [{"delta": {"type": "deposit", "usdc": "1000"}},
              {"delta": {"type": "accountClassTransfer", "usdc": "500"}}]
    assert la.counterparty_spread(ledger, WALLET) == (0, 0)


def test_spread_is_case_insensitive():
    assert la.counterparty_spread([_send(WALLET.upper(), _peer(1))], WALLET) == (1, 0)


def test_high_fanout_is_a_service():
    assert "fan-out" in la.hl_service_reason(out_degree=583, in_degree=1)


def test_high_fanin_is_a_service():
    assert "fan-in" in la.hl_service_reason(out_degree=0, in_degree=15185)


def test_many_to_many_is_a_service():
    assert "many-to-many" in la.hl_service_reason(out_degree=30, in_degree=30)


def test_an_ordinary_wallet_is_not_a_service():
    """The guard that matters most — over-calling this turns a genuine migration
    destination into an excluded address and ends the trail at the wallet we
    were looking for."""
    assert la.hl_service_reason(out_degree=3, in_degree=2) is None


def test_probe_flags_a_high_fanout_address():
    ledger = [_send(WALLET, _peer(i)) for i in range(60)]
    services, errors = la.probe_hl_services([WALLET], fetch=lambda a: ledger)
    assert WALLET in services and not errors


def test_probe_leaves_an_ordinary_wallet_alone():
    services, errors = la.probe_hl_services(
        [WALLET], fetch=lambda a: [_send(WALLET, _peer(1))])
    assert services == {} and errors == {}


def test_a_failed_probe_is_an_error_not_a_verdict():
    """'We could not tell' must never serialise as 'we checked and it is a
    wallet' — a rate-limited run would silently promote an exchange to a lead."""
    def boom(addr):
        raise TimeoutError("rate limited")

    services, errors = la.probe_hl_services([WALLET], fetch=boom)
    assert services == {}
    assert WALLET in errors and "rate limited" in errors[WALLET]


def test_a_malformed_payload_is_an_error_not_a_verdict():
    services, errors = la.probe_hl_services([WALLET], fetch=lambda a: {"error": "nope"})
    assert services == {}
    assert WALLET in errors


def test_probe_respects_its_call_budget():
    calls = []

    def counting(addr):
        calls.append(addr)
        return []

    la.probe_hl_services([_peer(i) for i in range(100)], max_probes=5, fetch=counting)
    assert len(calls) == 5


def test_probe_thresholds_match_the_graph_detector():
    """Both must agree about the same address on the same numbers, or the graph
    and the analyzer will label it differently depending on which ran."""
    import src.transfer_graph as tg
    assert la.hl_service_reason(
        out_degree=tg.DEFAULTS["service_fanout"] * 2, in_degree=0,
        fanout=tg.DEFAULTS["service_fanout"], fanin=tg.DEFAULTS["service_fanin"])


def test_a_measured_reason_survives_into_the_graph():
    """An address detected by HL fan-out reaches detect_services indistinguishable
    from one typed into config. If the generic label wins, the evidence that
    actually excluded a $45M counterparty disappears from the audit trail."""
    import src.transfer_graph as tg
    edges = [{"src": WALLET, "dst": _peer(1), "amount_usd": 10.0,
              "discovery_source": tg.SRC_L1}]
    services = tg.detect_services(
        edges, {WALLET}, reasons={WALLET: "high HL fan-out (583 distinct recipients)"})
    assert services[WALLET] == "high HL fan-out (583 distinct recipients)"


def test_a_configured_address_without_a_measured_reason_keeps_the_generic_label():
    import src.transfer_graph as tg
    edges = [{"src": WALLET, "dst": _peer(1), "amount_usd": 10.0,
              "discovery_source": tg.SRC_L1}]
    services = tg.detect_services(edges, {WALLET})
    assert "configured service address" in services[WALLET]
