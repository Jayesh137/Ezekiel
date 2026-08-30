# tests/test_wallet_links.py
"""Every user-facing wallet address must link to its Hypurrscan page.

The failure this guards against is subtle: a link built from the SHORTENED label
the reader sees ("0x45d2...4029") looks right on screen and 404s when clicked.
So the rule under test is that the href always carries the complete address, and
that anything incomplete renders as plain text rather than a dead link.

Covers both sides — the Python message helpers and, by reading the source, the
Svelte call sites — because an address in an email and the same address on the
dashboard have to resolve to the same page.
"""

import re
from pathlib import Path

import pytest

from src.links import HYPURRSCAN, address_line, address_path, address_url, is_address

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "src"

FULL = "0x45d26f28196d226497130c4bac709d808fed4029"
CHECKSUMMED = "0x45D26f28196D226497130C4bAC709d808FeD4029"
SHORT = "0x45d2...4029"


# --- 1. full address -> canonical URL ---------------------------------------

def test_full_address_produces_hypurrscan_url():
    assert address_url(FULL) == f"https://hypurrscan.io/address/{FULL}"


def test_no_tracking_parameters_are_added():
    url = address_url(FULL)
    assert "?" not in url and "#" not in url


# --- 2. shortened label still links with the full address -------------------

def test_shortened_label_never_reaches_the_href():
    """The visible label may be truncated; the link may not be."""
    assert address_url(SHORT) is None, "a shortened label must not produce a link"
    line = address_line(FULL, "Wallet")
    assert FULL in line
    assert SHORT not in line
    assert f"{HYPURRSCAN}/address/{FULL}" in line


# --- 3. casing preserved ----------------------------------------------------

def test_checksummed_casing_is_preserved():
    assert address_url(CHECKSUMMED).endswith(CHECKSUMMED)
    assert address_url(CHECKSUMMED) != address_url(FULL)


# --- 4 & 5. empty / partial are not linked ----------------------------------

def test_empty_address_is_not_linked():
    for value in ("", None, "   "):
        assert address_url(value) is None
        assert not is_address(value)


def test_truncated_or_malformed_address_is_not_linked():
    for value in (SHORT, "0x45d2", FULL[:-1], FULL + "ff", "45d26f28" * 5,
                  "0xZZZZ6f28196d226497130c4bac709d808fed4029", 12345, ["0x1"]):
        assert address_url(value) is None, f"{value!r} must not be linkable"


def test_unlinkable_value_still_reports_the_address_as_text():
    line = address_line(SHORT, "Candidate")
    assert line == f"Candidate: {SHORT}"
    assert "http" not in line, "no link may be emitted for a partial address"
    assert address_line("", "Wallet") == "Wallet: (unknown)"


def test_address_is_url_encoded():
    # Defensive: a value that survives validation is still encoded on the way out.
    assert " " not in (address_url(FULL) or "")
    assert address_url("0x" + "0" * 40) == f"{HYPURRSCAN}/address/0x{'0' * 40}"


# --- 6-9. real payload shapes link correctly --------------------------------

def test_target_wallet_links():
    assert address_url(FULL).startswith(f"{HYPURRSCAN}/address/")


def test_scanner_candidate_links():
    candidate = {"wallet": "0x" + "a" * 40, "best_score": 0.91}
    assert address_url(candidate["wallet"]) == f"{HYPURRSCAN}/address/0x{'a' * 40}"


def test_transfer_graph_source_destination_and_every_hop_link():
    hops = [
        {"src": FULL, "dst": "0x" + "a" * 40},
        {"src": "0x" + "a" * 40, "dst": "0x" + "b" * 40},
        {"src": "0x" + "b" * 40, "dst": "0x" + "c" * 40},
    ]
    for h in hops:
        assert address_url(h["src"]), f"unlinked source {h['src']}"
        assert address_url(h["dst"]), f"unlinked destination {h['dst']}"

    walk = [FULL] + [h["dst"] for h in hops]
    rendered = address_path(walk)
    for addr in walk:
        assert addr in rendered
        assert f"{HYPURRSCAN}/address/{addr}" in rendered
    assert rendered.count(f"{HYPURRSCAN}/address/") == len(walk)


def test_migration_candidate_and_successor_wallets_link():
    for node in ({"wallet": "0x" + "d" * 40, "classification": "MIGRATION_CANDIDATE"},
                 {"wallet": "0x" + "e" * 40,
                  "lifecycle": {"state": "HIGH_CONFIDENCE_SUCCESSOR"}}):
        assert address_url(node["wallet"]).startswith(f"{HYPURRSCAN}/address/")


# --- 10. Python-generated messages ------------------------------------------

def test_generated_alert_bodies_use_hypurrscan(monkeypatch):
    """Capture what the alert layer would send, without sending anything."""
    import src.alerts as alerts

    captured = {}

    def fake_send(subject, body):
        captured["subject"] = subject
        captured["body"] = body
        return True

    monkeypatch.setattr(alerts, "send_alert",
                        lambda subject, body, html_body=None: fake_send(subject, body))
    monkeypatch.setattr(alerts, "_send_with_cooldown",
                        lambda key, hours, subject, body: fake_send(subject, body))

    alerts.alert_transfer_graph_discovery(
        {"wallet": FULL, "classification": "MIGRATION_CANDIDATE", "confidence": 0.82,
         "depth": 2, "path": [FULL, "0x" + "a" * 40, "0x" + "b" * 40],
         "confidence_reasons": ["direct funding"], "first_seen": "x", "last_seen": "y",
         "totals": {}},
        ["newly discovered"], [])
    body = captured["body"]
    assert f"{HYPURRSCAN}/address/{FULL}" in body
    assert f"{HYPURRSCAN}/address/0x{'a' * 40}" in body, "every hop must link"
    assert f"{HYPURRSCAN}/address/0x{'b' * 40}" in body
    assert "etherscan.io" not in body and "arbiscan.io/address" not in body


def test_alert_module_builds_no_address_url_by_hand():
    src = (ROOT / "src" / "alerts.py").read_text(encoding="utf-8")
    assert "hypurrscan.io" not in src, "alerts.py must go through src/links.py"
    assert re.search(r"https?://\S*/address/", src) is None


# --- 11 & 12. repository-wide guarantees ------------------------------------

SVELTE_FILES = sorted(DASHBOARD.rglob("*.svelte"))
JS_FILES = sorted(DASHBOARD.rglob("*.js"))


def test_no_wallet_href_points_at_another_explorer():
    """No source file may send an ADDRESS anywhere but Hypurrscan."""
    offenders = []
    pattern = re.compile(
        r"(etherscan\.io/address|arbiscan\.io/address|"
        r"app\.hyperliquid\.xyz/explorer/address|blockscan\.com/address|"
        r"debank\.com/profile|zapper\.[a-z]+/account)", re.I)
    for f in [*SVELTE_FILES, *JS_FILES, *(ROOT / "src").rglob("*.py")]:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{f.relative_to(ROOT)}:{i}")
    assert not offenders, f"non-Hypurrscan wallet links: {offenders}"


def test_routes_do_not_hand_build_address_anchors():
    """Address links must go through the Addr component, so the full-address
    rule is enforced in exactly one place."""
    offenders = []
    for f in SVELTE_FILES:
        if f.name == "Addr.svelte":
            continue
        text = f.read_text(encoding="utf-8")
        offenders.extend(
            f"{f.relative_to(ROOT)}: {m.group(0)[:60]}"
            for m in re.finditer(r"href=[\"{][^\"}\n]*/address/[^\"}\n]*", text))
    assert not offenders, f"hand-built address anchors: {offenders}"


def test_addr_component_enforces_full_address_and_safe_target():
    text = (DASHBOARD / "lib" / "Addr.svelte").read_text(encoding="utf-8")
    assert "addressUrl(address)" in text, "href must come from the validated helper"
    assert 'target="_blank"' in text
    assert 'rel="noopener noreferrer"' in text
    assert "{:else}" in text, "an unlinkable address must fall back to plain text"
    # The label may be shortened; the href must not be derived from it.
    assert "addressUrl(shortAddr" not in text
    assert "href={shortAddr" not in text


def test_internal_navigation_links_are_unchanged():
    """Only explorer destinations were in scope: in-app routes must still be
    relative, and transaction links must still point at a tx page."""
    home = (DASHBOARD / "routes" / "+page.svelte").read_text(encoding="utf-8")
    assert 'href="{base}/recovery"' in home, "in-app navigation must stay internal"

    api = (DASHBOARD / "lib" / "api.js").read_text(encoding="utf-8")
    assert "/tx/" in api, "transaction links must remain transaction links"
    assert "arbiscan.io/tx/" in api, "Arbitrum tx hashes only resolve on Arbiscan"


# --- 13. all-chain address reuse in linkage ---------------------------------

def test_outbound_addresses_come_from_every_chain_without_api_calls(tmp_path, monkeypatch):
    """Address reuse is the strongest linkage signal available, and it was
    limited to Arbitrum USDC. The substrate already holds every chain, so
    widening it costs nothing."""
    import json

    from src import linkage
    from src.chain import collect

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
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

    from src import linkage
    from src.chain import collect
    from src.utils import load_config

    monkeypatch.setattr(collect, "TRANSFERS_DIR", tmp_path / "transfers")
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
