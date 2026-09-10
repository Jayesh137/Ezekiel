#!/usr/bin/env python3
"""Human-chosen names on Hyperliquid, searched for the naming family.

Display names on the leaderboard (1,441 of 45,005 rows carried one on
2026-09-10) and vault names are the labels people pick for themselves. The
GCR corpus has a recurring token — Rebirth — and a handful of aliases; a
match is a reason to look, never a conclusion (the one hit on 2026-09-10,
"ACLGCR", was an empty account).
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.alerts import alert_name_hit
from src.scanner import fetch_leaderboard
from src.utils import DATA_DIR, hl_post, save_latest

NAMES_DIR = DATA_DIR / "names"
PATTERN = re.compile(r"rebirth|gigantic|\bgcr\b|ezekiel|mecca|trueshiba|kabosu|stonehenge|"
                     r"sad ?doge|ming ?x", re.I)


def hits_in(named: list[tuple[str, str, str]]) -> list[dict]:
    """(address, name, source) rows whose name matches the family."""
    out = []
    for addr, name, source in named:
        if name and PATTERN.search(name):
            out.append({"address": (addr or "").lower(), "name": name, "source": source})
    return out


def main() -> int:
    rows = fetch_leaderboard()
    named = [(r.get("ethAddress"), r["displayName"], "leaderboard")
             for r in rows or [] if isinstance(r, dict) and r.get("displayName")]
    vaults = hl_post({"type": "vaultSummaries"})
    named.extend((v.get("vaultAddress"), v["name"], "vault")
                 for v in (vaults if isinstance(vaults, list) else [])
                 if isinstance(v, dict) and v.get("name"))
    hits = hits_in(named)
    previous = set()
    try:
        with open(NAMES_DIR / "latest.json") as f:
            previous = {h["address"] for h in json.load(f).get("hits", [])}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    save_latest(str(NAMES_DIR), {"computed_at": datetime.now(UTC).isoformat(),
                                 "names_searched": len(named), "hits": hits})
    print(f"[names] {len(named)} human-chosen name(s) searched, {len(hits)} hit(s)")
    for h in hits:
        print(f"[names]   {h['source']:<11} {h['address']} '{h['name']}'")
        if h["address"] not in previous:
            alert_name_hit(h["address"], h["name"], h["source"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
