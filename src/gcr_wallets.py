# src/gcr_wallets.py
"""Watch the Ethereum addresses that GCR's own words led to.

Everything else in the GCR material is STYLE: it compares trading habits and can
only ever nudge the operator's 55-75% prior. This is the one part that is FLOW.
An address either transacts with the tracked wallet or it does not, and that
question has an answer rather than a verdict.

The chain, each step read from the chain rather than from an article:

    img163  GCR, 2021-08-27, with a truncated opensea.io/assets/0xabefb... link:
            "Sold my SAD DOGE [KABOSU] NFT for 2 million USDC. Purchase price
             was 15 ETH in June. Hope this nice doggo finds a comfortable home
             to rest with @TwoDollaHotDoge"
    ->      0xabefb... is Zora: Media; the token is 3372
    ->      every Transfer of token 3372, in order
    ->      0x246eA68F... won it in June for 15.6558675 ETH and sold it on
            2021-08-27 for exactly 2,000,000 USDC to the under-bidder

Four public claims -- purchase month, purchase price, sale price, buyer -- land
on one address. That is the confirmation; no single claim would have been enough.

**None of these has ever touched Hyperliquid, and none is a counterparty of the
target.** Checked 2026-09-10, with every endpoint answering rather than failing.
So this module is a tripwire, not a finding: its whole value is the day one of
them moves. If a confirmed GCR address ever appears on Hyperliquid, or in the
target's transfer graph, that is the strongest evidence this project could get
about who the tracked wallet is -- in either direction.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

LABELS_PATH = DATA_DIR / "labels" / "gcr_addresses.json"
WATCH_DIR = DATA_DIR / "gcr_wallets"

# Tiers whose appearance anywhere near the target is worth waking someone for.
# 'exchange_deposit' is included deliberately: a CEX deposit address belongs to
# ONE account, so the target sending to GCR's deposit address would be a direct
# link between the tracked wallet and GCR's exchange account.
ACTIONABLE_TIERS = {"confirmed", "linked", "exchange_deposit"}


def load_addresses(path: Path | None = None) -> dict:
    try:
        with open(path or LABELS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def watched(data: dict | None = None, tiers: set | None = None) -> dict:
    """Address -> record, for the tiers worth acting on."""
    data = data if data is not None else load_addresses()
    tiers = tiers if tiers is not None else ACTIONABLE_TIERS
    return {a.lower(): rec
            for a, rec in (data.get("addresses") or {}).items()
            if rec.get("tier") in tiers}


def infrastructure(data: dict | None = None) -> set:
    """Addresses explicitly recorded as shared infrastructure.

    Two of these — Binance hot wallets — ARE counterparties of the target, and
    matching on them would manufacture a link out of nothing. Kept as data so
    the exclusion is auditable rather than a magic list in code.
    """
    data = data if data is not None else load_addresses()
    return {a.lower() for a in (data.get("not_gcr") or {})}


def check_against_graph(graph_addresses, data: dict | None = None) -> list[dict]:
    """Which watched addresses appear in the target's graph.

    `graph_addresses` is any iterable of addresses the target has touched.
    Infrastructure is removed first — a shared Binance hot wallet is not a link.
    """
    data = data if data is not None else load_addresses()
    infra = infrastructure(data)
    seen = {str(a).lower() for a in (graph_addresses or [])} - infra
    return [{"address": a, "tier": rec.get("tier"), "role": rec.get("role"),
             "why": "appears in the target's transfer graph"}
            for a, rec in watched(data).items() if a in seen]


def check_hyperliquid(states: dict, data: dict | None = None) -> list[dict]:
    """Which watched addresses show real Hyperliquid activity.

    `states` maps address -> {"fills": n, "ledger": n, "account_value": str,
    "read_ok": bool}. A failed read is NOT absence: `read_ok` False is reported
    as unknown rather than counted as clean, because "we could not tell" and
    "there is nothing there" are different answers.

    Airdrop spam does not count. 0x398d2824... holds three unsolicited inbound
    spotTransfers and one automatic dust conversion, which is not the address
    trading — treating that as GCR on Hyperliquid would be a false positive.
    """
    data = data if data is not None else load_addresses()
    out = []
    for addr, rec in watched(data).items():
        st = (states or {}).get(addr)
        if st is None or not st.get("read_ok", True):
            out.append({"address": addr, "tier": rec.get("tier"),
                        "status": "unknown", "why": "Hyperliquid read failed"})
            continue
        traded = int(st.get("fills") or 0) > 0
        moved = int(st.get("outbound_ledger") or 0) > 0
        try:
            value = float(st.get("account_value") or 0)
        except (TypeError, ValueError):
            value = 0.0
        if traded or moved or value > 0:
            out.append({"address": addr, "tier": rec.get("tier"),
                        "status": "ACTIVE", "account_value": value,
                        "fills": st.get("fills"),
                        "why": "watched GCR address is live on Hyperliquid"})
    return out


def build_report(graph_addresses=None, hl_states=None,
                 data: dict | None = None) -> dict:
    data = data if data is not None else load_addresses()
    graph_hits = check_against_graph(graph_addresses or [], data)
    hl_hits = check_hyperliquid(hl_states or {}, data)
    live = [h for h in hl_hits if h["status"] == "ACTIVE"]
    unknown = [h for h in hl_hits if h["status"] == "unknown"]
    return {
        "computed_at": datetime.now(UTC).isoformat(),
        "watched": len(watched(data)),
        "graph_hits": graph_hits,
        "hyperliquid_hits": live,
        "unreadable": unknown,
        "clean": not graph_hits and not live,
        "reading": _reading(graph_hits, live, unknown),
    }


def _reading(graph_hits, live, unknown) -> str:
    if graph_hits or live:
        return ("A GCR-controlled address has moved into range of the target. "
                "This is flow, not style — treat it as the strongest evidence "
                "the project has produced and verify it by hand.")
    if unknown:
        return (f"Nothing found, but {len(unknown)} address(es) could not be "
                f"read — this is not a clean result.")
    return ("No GCR address touches the target or Hyperliquid. Expected, and "
            "not evidence against the hypothesis: the confirmed wallet went "
            "quiet in 2022 and the treasury in Dec 2024, while the target's "
            "history starts 2026-02-05, so there is nothing to connect yet.")


def save(report: dict) -> None:
    save_latest(str(WATCH_DIR), report)
