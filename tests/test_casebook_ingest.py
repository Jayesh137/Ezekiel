"""One roster reading -> the casebook, the same for live runs and the backfill (spec §4)."""

from src.casebook import ingest

T = "0x45d26f28196d226497130c4bac709d808fed4029"
SELF = "0xf078969e55cabf9ae3f26afeb5ec627b4430f19e"
A, B, C = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
CONFIG = {"target_wallet": T, "known_self_wallets": [SELF]}
H = 3_600_000
T0 = 1_790_000_000_000


def roster(*rows):
    return {"computed_at": "2026-09-21T13:33:20+00:00", "wallets": list(rows)}


def test_leads_open_cases_and_noise_does_not():
    cases = {}
    out = ingest.apply_roster(cases, roster(
        {"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]},
        {"wallet": B, "tier": "WATCH", "evidence": {"graph_reach_only": True}},
        {"wallet": T, "tier": "CONFIRMED"},
        {"wallet": USDC_BASE, "tier": "POSSIBLE", "vectors": ["transfer"]}),
        CONFIG, at_ms=T0, origin="live")
    assert set(cases) == {A, SELF}                       # SELF opened from config
    assert out["opened"] == 2 and out["rows"] == 4 and out["rejected_count"] == 1
    assert out["rejected"][USDC_BASE]["reason"].startswith("not a wallet")
    assert cases[SELF]["known"] == "config:known_self"


def test_an_open_case_keeps_its_evidence_when_the_roster_drops_it():
    cases = {}
    ingest.apply_roster(cases, roster({"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}),
                        CONFIG, at_ms=T0, origin="live")
    ingest.apply_roster(cases, roster(), CONFIG, at_ms=T0 + H, origin="live")
    out = ingest.apply_roster(cases, roster(), CONFIG, at_ms=T0 + 30 * H, origin="live")
    assert cases[A]["evidence"]["direct_transfer"]["status"] == "lapsed"
    assert any(e["kind"] == "evidence_lapsed" and e["address"] == A for e in out["events"])


def test_a_case_the_filters_now_reject_is_excluded_then_readmitted():
    cases = {}
    lead = {"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}
    ingest.apply_roster(cases, roster(lead), CONFIG, at_ms=T0, origin="live")
    busy = {"wallet": A, "tier": "INFRASTRUCTURE", "is_service": True, "vectors": ["transfer"],
            "evidence": {"service_reason": "global activity: 590,836 txs"}}
    out = ingest.apply_roster(cases, roster(busy), CONFIG, at_ms=T0 + H, origin="live")
    assert cases[A]["excluded"]["reason"] == "service: global activity: 590,836 txs"
    assert any(e["kind"] == "case_excluded" for e in out["events"])
    assert cases[A]["roster"]["tier"] == "INFRASTRUCTURE"
    out = ingest.apply_roster(cases, roster(lead), CONFIG, at_ms=T0 + 2 * H, origin="live")
    assert cases[A]["excluded"] is None
    assert any(e["kind"] == "case_readmitted" for e in out["events"])


def test_a_detector_recheck_refutes_through_refuted_by():
    cases = {}
    row = {"wallet": A, "tier": "WATCH", "evidence": {"dormancy_handoff": {"score": 0.5}}}
    ingest.apply_roster(cases, roster(row), CONFIG, at_ms=T0, origin="live")
    gone = {"wallet": A, "tier": "WATCH"}
    ingest.apply_roster(cases, roster(gone), CONFIG, at_ms=T0 + H, origin="live",
                        refuted_by={"dormancy_handoff": {A}})
    ingest.apply_roster(cases, roster(gone), CONFIG, at_ms=T0 + 30 * H, origin="live")
    assert cases[A]["evidence"]["dormancy_handoff"]["status"] == "refuted"


def test_blocked_addresses_are_never_touched():
    cases = {}
    out = ingest.apply_roster(cases, roster({"wallet": A, "tier": "POSSIBLE", "vectors": ["transfer"]}),
                              CONFIG, at_ms=T0, origin="live", blocked={A})
    assert A not in cases and not any(e["address"] == A for e in out["events"])


def test_watch_wallets_open_a_case_without_any_roster_row():
    cases = {}
    ingest.apply_roster(cases, roster(), {**CONFIG, "watch_wallets": [{"address": C, "why": "x"}]},
                        at_ms=T0, origin="live")
    assert cases[C]["opened_by"] == ["config:watch"] and cases[C]["pinned"] == "config:watch"


def test_rejections_are_sticky_and_bounded():
    rejected = {}
    rows = [{"wallet": f"0x{2 ** 100 + i:040x}", "tier": "INFRASTRUCTURE", "is_service": True,
             "vectors": ["linkage"]} for i in range(ingest.MAX_REJECTED + 20)]
    ingest.apply_roster({}, roster(*rows), CONFIG, at_ms=T0, origin="live", rejected=rejected)
    assert len(rejected) == ingest.MAX_REJECTED
