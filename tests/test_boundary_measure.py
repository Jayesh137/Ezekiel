"""What the shared caches already measured, read without spending a lookup."""

import json

from src.boundary import measure

BUSY, QUIET, CON, UNSEEN = "0x" + "1" * 40, "0x" + "3" * 40, "0x" + "4" * 40, "0x" + "5" * 40
HOT, SVC, INFRA = "0x" + "7" * 40, "0x" + "8" * 40, "0x" + "9" * 40


def test_measured_reads_activity_and_bytecode_caches(tmp_path):
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "address_activity.json").write_text(json.dumps({
        f"arbitrum:{BUSY}": {"txs": 900_000, "token_transfers": 10, "is_contract": False},
        f"ethereum:{QUIET}": {"txs": 40, "token_transfers": 12, "is_contract": False},
        f"base:{CON}": {"txs": 5, "token_transfers": 5, "is_contract": True}}))
    (tmp_path / "labels" / "code_cache.json").write_text(json.dumps({f"arbitrum:{QUIET}": False}))
    contract, busy, known = measure.measured(tmp_path)
    assert busy(BUSY) and not busy(QUIET) and not busy(UNSEEN)
    assert contract(CON) and not contract(QUIET)
    assert known(QUIET) and known(BUSY) and not known(UNSEEN)


def test_services_come_from_labels_config_and_roster_infrastructure(tmp_path):
    (tmp_path / "labels").mkdir()
    (tmp_path / "labels" / "entities.json").write_text(json.dumps({"entities": [
        {"address": HOT, "category": "cex_hot"}, {"address": SVC, "category": "bridge"}]}))
    (tmp_path / "roster").mkdir()
    (tmp_path / "roster" / "latest.json").write_text(json.dumps({"wallets": [
        {"wallet": INFRA, "tier": "INFRASTRUCTURE"}, {"wallet": UNSEEN, "tier": "WATCH"}]}))
    services, hot = measure.load_services({"known_service_addresses": [BUSY],
                                           "excluded_addresses": []}, tmp_path)
    assert hot == {HOT}
    assert {HOT, SVC, INFRA, BUSY} <= services and UNSEEN not in services
