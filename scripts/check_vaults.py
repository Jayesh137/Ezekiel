#!/usr/bin/env python3
"""A vault led by a cluster wallet is a tradeable address of his.

`vaultSummaries` lists every vault with its leader; the treasury already uses
HLP as a depositor. A vault whose LEADER is the target or a known wallet is a
new address the owner could follow directly, and it never appears in fills
or ledgers as a counterparty. One call, no key.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_vault_led
from src.utils import DATA_DIR, hl_post, load_config, save_latest

VAULTS_DIR = DATA_DIR / "vault_watch"


def led_by(summaries, cluster: set) -> list[dict]:
    out = []
    for v in summaries if isinstance(summaries, list) else []:
        if not isinstance(v, dict):
            continue
        leader = (v.get("leader") or "").lower()
        if leader in cluster:
            out.append({"vault": (v.get("vaultAddress") or "").lower(), "leader": leader,
                        "name": v.get("name"), "tvl": v.get("tvl"),
                        "created": v.get("createTimeMillis"), "closed": v.get("isClosed")})
    return out


def main() -> int:
    config = load_config()
    cluster = {(config.get("target_wallet") or "").lower()}
    cluster |= {(w or "").lower() for w in config.get("known_self_wallets", [])}
    summaries = hl_post({"type": "vaultSummaries"})
    if not isinstance(summaries, list):
        print("[vaults] vaultSummaries unreadable — falling back to the identity report")
        summaries = []
    hits = led_by(summaries, cluster)
    # vaultSummaries answered [] on 2026-09-10, so the authoritative source is
    # webData2.leadingVaults per cluster wallet, recorded by check_identity.
    seen = {h["vault"] for h in hits}
    try:
        with open(DATA_DIR / "identity" / "latest.json") as f:
            identities = json.load(f).get("identities") or {}
    except (OSError, ValueError, AttributeError):
        identities = {}
    for addr in sorted(cluster):
        for vault in (identities.get(addr) or {}).get("leading_vaults") or []:
            v = (vault or "").lower()
            if v and v not in seen:
                seen.add(v)
                hits.append({"vault": v, "leader": addr, "name": None, "tvl": None,
                             "created": None, "closed": None, "source": "webData2"})
    previous = set()
    try:
        with open(VAULTS_DIR / "latest.json") as f:
            previous = {h["vault"] for h in json.load(f).get("led_by_cluster", [])}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    save_latest(str(VAULTS_DIR), {"computed_at": datetime.now(UTC).isoformat(),
                                  "vaults_listed": len(summaries), "led_by_cluster": hits})
    print(f"[vaults] {len(summaries)} vault(s) listed by vaultSummaries; {len(hits)} led by a "
          f"cluster wallet (including webData2.leadingVaults)")
    for h in hits:
        print(f"[vaults]   {h['vault']} '{h['name']}' led by {h['leader'][:12]}... tvl={h['tvl']}")
        if h["vault"] not in previous:
            alert_vault_led(h["vault"], h["leader"], h.get("name"), h.get("tvl"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
