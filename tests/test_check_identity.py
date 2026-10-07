"""When the identity sweep reads a wallet again: a week on, or before it had the portfolio fields.

`total_value` arrived with `parse_activity`. A non-core row probed earlier keeps the perp-margin
reading, and the study set judges Hyperliquid presence on the total, so such a row is re-read on
the next pass instead of up to RECHECK_DAYS later (a lead holding $9.37M in spot read as empty).
"""

import json
import types
from datetime import UTC, datetime, timedelta

import pytest

import scripts.check_identity as ci

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def row(age, **fields):
    return {"checked_at": (NOW - age).isoformat(), **fields}


def test_a_fresh_row_with_no_total_value_key_is_stale():
    assert ci._stale(row(timedelta(hours=1)), NOW) is True
    assert ci._stale(row(timedelta(hours=1), account_value=0.0, birth_ms=5), NOW) is True
    assert ci._stale({}, NOW) is True                       # never probed


def test_a_fresh_row_with_the_key_is_not_stale_even_when_the_probe_stored_none():
    # The KEY marks a probe that has run since the fields existed. A probe whose portfolio
    # read failed stores None; re-reading it early would make that wallet a standing cost.
    for stored in (None, 0.0, 9_370_000.5):
        assert ci._stale(row(timedelta(hours=1), total_value=stored), NOW) is False


def test_an_old_row_is_stale_whatever_it_carries():
    assert ci._stale(row(timedelta(days=7), total_value=5.0), NOW) is True
    assert ci._stale(row(timedelta(days=30), total_value=None), NOW) is True
    assert ci._stale(row(timedelta(days=6, hours=23), total_value=5.0), NOW) is False


def test_a_row_with_no_usable_reading_time_is_stale():
    assert ci._stale({"total_value": 5.0}, NOW) is True
    assert ci._stale({"total_value": 5.0, "checked_at": "not a date"}, NOW) is True
    assert ci._stale({"total_value": 5.0, "checked_at": None}, NOW) is True
    assert ci._stale(None, NOW) is True


# --- the graph's conduits are read too ------------------------------------------------
#
# `transfer_graph` exempts a wallet that trades on Hyperliquid from the conduit pass, and
# it judges that from this sweep's identity rows. The sweep used to read only the roster's
# non-INFRASTRUCTURE rows, and a conduit is INFRASTRUCTURE in the roster or absent from it,
# so none was ever read: measured 2026-10-07, 9 of the graph's 145 conduits were
# Hyperliquid accounts that traded $196.5M in 30 days, and one sat in the rota.


def addr(n: int) -> str:
    return "0x" + f"{n:040x}"


TARGET, SELF_WALLET = addr(0xF0), addr(0xF1)
CONFIG = {"target_wallet": TARGET, "known_self_wallets": [SELF_WALLET]}
LEAD, WATCHED, WATCHED_TOO = addr(0x10), addr(0x20), addr(0x21)
INFRA_CONDUIT, ABSENT_CONDUIT = addr(0xC1), addr(0xC2)         # sorted: the first is smaller
EXCHANGE = addr(0xE0)
CONDUIT = "conduit: forwards 99% of the $2,349,224 it receives straight to infrastructure"
CONFIGURED = "configured service address (exchange/bridge/contract)"


def tiered(wallet, tier):
    return {"wallet": wallet, "tier": tier}


def serve(tmp_path, monkeypatch, *, roster=None, services=None, nodes=None, graph=None):
    """Point the script at a tmp data dir. `roster` is a list of rows, `services` the graph's
    map and `nodes` its node wallets (written as the graph writes them: rows keyed `wallet`);
    `graph` is raw file text for a malformed one. Nothing given means no such file."""
    monkeypatch.setattr(ci, "DATA_DIR", tmp_path)
    if roster is not None:
        (tmp_path / "roster").mkdir(parents=True, exist_ok=True)
        (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": roster}))
    if services is not None or nodes is not None or graph is not None:
        (tmp_path / "transfer_graph").mkdir(parents=True, exist_ok=True)
        doc = {}
        if services is not None:
            doc["services"] = services
        if nodes is not None:
            doc["nodes"] = [{"wallet": w, "classification": "SERVICE", "depth": 1} for w in nodes]
        text = graph if graph is not None else json.dumps(doc)
        (tmp_path / "transfer_graph" / "latest.json").write_text(text)


def test_graph_conduits_are_the_services_whose_reason_starts_with_conduit(tmp_path, monkeypatch):
    services = {
        "0x" + ABSENT_CONDUIT[2:].upper(): CONDUIT,                # an upper-case key is lower-cased
        INFRA_CONDUIT: "Conduit: forwards 96% of the $1,414,553 it receives",   # case-insensitive
        EXCHANGE: CONFIGURED,
        addr(0xE1): "inferred exchange deposit address: forwards 96% to 0xca077a2654...",
        addr(0xE2): "global activity: 1,200,000 transactions, a busy conduit",   # contains, not starts
        addr(0xE3): None,                                           # not a reason at all
        addr(0xE4): {"reason": CONDUIT},                            # not the shape the graph writes
    }
    serve(tmp_path, monkeypatch, services=services)

    assert ci.graph_conduits() == [INFRA_CONDUIT, ABSENT_CONDUIT]   # lower-cased and sorted


def test_an_empty_services_map_is_no_conduits_and_says_nothing(tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, services={})

    assert ci.graph_conduits() == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("graph", [
    None,                                  # no file at all
    "", "{not json", "[1, 2]", "null", '"text"',
    "{}", '{"services": null}', '{"services": []}', '{"services": "x"}',
], ids=["missing", "empty", "not-json", "list", "null", "string",
        "no-services", "services-null", "services-list", "services-string"])
def test_a_missing_or_malformed_graph_file_is_no_conduits_and_one_logged_line(
        tmp_path, monkeypatch, capsys, graph):
    serve(tmp_path, monkeypatch, graph=graph)

    assert ci.graph_conduits() == []
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("[identity]")


def test_conduits_are_queued_right_after_the_leads(tmp_path, monkeypatch):
    # The roster lists the WATCH row BEFORE the lead, so a queue that merely kept roster
    # order and slotted the conduits in would put them behind it.
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(LEAD, "POSSIBLE"),
                  tiered(INFRA_CONDUIT, "INFRASTRUCTURE"), tiered(EXCHANGE, "INFRASTRUCTURE")],
          services={ABSENT_CONDUIT: CONDUIT, INFRA_CONDUIT: CONDUIT, EXCHANGE: CONFIGURED})

    assert ci.candidates(CONFIG) == [LEAD, INFRA_CONDUIT, ABSENT_CONDUIT, WATCHED]


def test_leads_keep_roster_order_and_a_conduit_is_listed_once(tmp_path, monkeypatch):
    lead_b, lead_a = addr(0x12), addr(0x11)
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(lead_b, "PROBABLE"), tiered(WATCHED_TOO, "WATCH"),
                  tiered(lead_a, "CONFIRMED"), tiered(addr(0x13), "POSSIBLE")],
          # one conduit is also a lead, one is also a WATCH row
          services={addr(0x13): CONDUIT, WATCHED_TOO: CONDUIT})

    assert ci.candidates(CONFIG) == [lead_b, lead_a, addr(0x13), WATCHED_TOO, WATCHED]


def test_without_a_usable_graph_the_queue_is_the_roster_without_infrastructure(
        tmp_path, monkeypatch):
    roster = [tiered(LEAD, "POSSIBLE"), tiered(WATCHED, "WATCH"), tiered(EXCHANGE, "INFRASTRUCTURE")]
    for graph in (None, "{not json", "{}"):
        serve(tmp_path, monkeypatch, roster=roster, graph=graph)
        assert ci.candidates(CONFIG) == [LEAD, WATCHED]


def test_without_a_roster_the_conduits_are_still_queued(tmp_path, monkeypatch):
    serve(tmp_path, monkeypatch, services={ABSENT_CONDUIT: CONDUIT})

    assert ci.candidates(CONFIG) == [ABSENT_CONDUIT]


def test_the_cluster_never_appears_whatever_the_roster_and_graph_say_of_it(tmp_path, monkeypatch):
    serve(tmp_path, monkeypatch,
          roster=[tiered(TARGET, "CONFIRMED"), tiered("0x" + SELF_WALLET[2:].upper(), "POSSIBLE"),
                  tiered(LEAD, "POSSIBLE"), tiered(TARGET, "INFRASTRUCTURE")],
          services={TARGET: CONDUIT, SELF_WALLET: CONDUIT, ABSENT_CONDUIT: CONDUIT})

    assert ci.candidates(CONFIG) == [LEAD, ABSENT_CONDUIT]


def run_main(tmp_path, monkeypatch):
    """Run the sweep once with the probe faked: the addresses it probed, in order, and the report."""
    monkeypatch.setattr(ci, "IDENTITY_DIR", tmp_path / "identity")
    monkeypatch.setattr(ci, "load_config", lambda: CONFIG)
    monkeypatch.setattr(ci, "time", types.SimpleNamespace(sleep=lambda seconds: None))
    monkeypatch.setattr(ci, "alert_explicit_link", lambda *args, **kwargs: None)
    probed, saved = [], []

    def fake_probe(address, fetch, *, sleep=None):
        probed.append(address)
        return {"address": address, "read_ok": True, "role": "user", "total_value": 1.0,
                "month_volume": 0.0, "checked_at": datetime.now(UTC).isoformat()}

    monkeypatch.setattr(ci, "probe", fake_probe)
    monkeypatch.setattr(ci, "save", saved.append)

    assert ci.main() == 0
    return probed, saved[0]


def test_main_probes_the_conduits_right_after_the_cluster_and_the_leads(tmp_path, monkeypatch):
    conduits = [addr(0x1000 + i) for i in range(20)]               # more than one run can read
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(LEAD, "POSSIBLE")],
          services={a: CONDUIT for a in conduits})

    probed, report = run_main(tmp_path, monkeypatch)

    assert probed == [TARGET, SELF_WALLET, LEAD, *conduits[:ci.MAX_OTHERS - 1]]
    assert report["probed_this_run"] == 2 + ci.MAX_OTHERS


# --- the graph's nodes are read too, right after the leads ----------------------------------
#
# A wallet the target funded can start trading on Hyperliquid, and `transfer_graph`'s
# `trades_on_hl` can only say so for an address this sweep has read. Measured 2026-10-07:
# 182 of the graph's 299 nodes (all classified SERVICE) had never been probed, so whether a
# wallet funded by the target now trades could not be known for them. They are queued after
# the roster's leads and before the conduits, in the graph's own order (it sorts its most
# promising nodes first: classification rank, then confidence), and each address once.

NODE_A, NODE_B, NODE_C = addr(0x31), addr(0x30), addr(0x32)    # file order is not address order


def test_nodes_queue_after_the_leads_and_before_the_conduits(tmp_path, monkeypatch):
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(LEAD, "POSSIBLE"),
                  tiered(INFRA_CONDUIT, "INFRASTRUCTURE")],
          nodes=[NODE_A, NODE_B, NODE_C],
          services={ABSENT_CONDUIT: CONDUIT, INFRA_CONDUIT: CONDUIT})

    assert ci.candidates(CONFIG) == [LEAD, NODE_A, NODE_B, NODE_C,
                                     INFRA_CONDUIT, ABSENT_CONDUIT, WATCHED]


def test_a_node_that_is_also_a_lead_a_conduit_or_a_watch_row_is_listed_once(tmp_path, monkeypatch):
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(LEAD, "POSSIBLE"), tiered(WATCHED_TOO, "WATCH")],
          # the lead, a conduit and a WATCH row are each also a node, and the list repeats one
          nodes=[NODE_A, LEAD, ABSENT_CONDUIT, WATCHED_TOO, NODE_A],
          services={ABSENT_CONDUIT: CONDUIT, INFRA_CONDUIT: CONDUIT})

    # each at the first place it qualifies: the lead among the leads, the rest as nodes
    assert ci.candidates(CONFIG) == [LEAD, NODE_A, ABSENT_CONDUIT, WATCHED_TOO, INFRA_CONDUIT, WATCHED]


def test_the_cluster_never_appears_among_the_nodes(tmp_path, monkeypatch):
    serve(tmp_path, monkeypatch, services={},
          nodes=[TARGET, "0x" + SELF_WALLET[2:].upper(), NODE_A])

    assert ci.candidates(CONFIG) == [NODE_A]


def test_a_node_the_roster_calls_infrastructure_is_still_probed(tmp_path, monkeypatch):
    # Reach beats tidiness: the graph's own classification said nothing about Hyperliquid,
    # and the probe is what settles "does it trade there".
    serve(tmp_path, monkeypatch, roster=[tiered(NODE_A, "INFRASTRUCTURE")],
          services={}, nodes=[NODE_A])

    assert ci.candidates(CONFIG) == [NODE_A]


def test_node_wallets_are_lower_cased_and_rows_without_one_are_skipped(tmp_path, monkeypatch):
    mixed = addr(0xAB)                                              # hex letters, so the case shows
    serve(tmp_path, monkeypatch, graph=json.dumps({"services": {}, "nodes": [
        {"wallet": "0x" + mixed[2:].upper()}, {"wallet": None}, {"wallet": 7}, {"wallet": ""},
        {"address": NODE_B}, None, "x", [NODE_C], {"wallet": NODE_C}]}))

    assert mixed != "0x" + mixed[2:].upper()
    assert ci.graph_nodes() == [mixed, NODE_C]


def test_an_empty_node_list_is_no_nodes_and_says_nothing(tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, services={}, nodes=[])

    assert ci.graph_nodes() == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("graph", [
    None,                                  # no file at all
    "", "{not json", "[1, 2]", "null", '"text"',
    "{}", '{"nodes": null}', '{"nodes": {}}', '{"nodes": "x"}', '{"nodes": 3}',
], ids=["missing", "empty", "not-json", "list", "null", "string",
        "no-nodes", "nodes-null", "nodes-dict", "nodes-string", "nodes-number"])
def test_a_missing_or_malformed_graph_is_no_nodes_and_one_logged_line(
        tmp_path, monkeypatch, capsys, graph):
    serve(tmp_path, monkeypatch, graph=graph)

    assert ci.graph_nodes() == []
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("[identity]")


def test_an_unreadable_graph_is_reported_once_for_the_nodes_and_the_conduits(
        tmp_path, monkeypatch, capsys):
    serve(tmp_path, monkeypatch, roster=[tiered(LEAD, "POSSIBLE")], graph="{not json")

    assert ci.candidates(CONFIG) == [LEAD]
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_one_read_of_the_graph_file_serves_both_the_nodes_and_the_conduits(tmp_path, monkeypatch):
    # The file is ~35 MB and parses in over a second: candidates() must not read it twice.
    serve(tmp_path, monkeypatch, services={ABSENT_CONDUIT: CONDUIT}, nodes=[NODE_A])
    real_open, reads = open, []

    def counting_open(path, *args, **kwargs):
        if str(path).replace("\\", "/").endswith("transfer_graph/latest.json"):
            reads.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)

    assert ci.candidates(CONFIG) == [NODE_A, ABSENT_CONDUIT]
    assert len(reads) == 1


def test_main_probes_the_nodes_after_the_leads_and_before_the_conduits(tmp_path, monkeypatch):
    nodes = [addr(0x2000 + i) for i in range(20)]                  # more than one run can read
    conduits = [addr(0x1000 + i) for i in range(20)]
    serve(tmp_path, monkeypatch,
          roster=[tiered(WATCHED, "WATCH"), tiered(LEAD, "POSSIBLE")],
          nodes=nodes, services={a: CONDUIT for a in conduits})

    probed, report = run_main(tmp_path, monkeypatch)

    assert probed == [TARGET, SELF_WALLET, LEAD, *nodes[:ci.MAX_OTHERS - 1]]
    assert report["probed_this_run"] == 2 + ci.MAX_OTHERS
