#!/usr/bin/env python3
"""Ask every candidate what Hyperliquid declares about it: sub-accounts,
referral and vault deposits.

See `src/hl_surface.py` for what each finding is worth. Free: three calls per
wallet plus one per unmeasured referrer, no key, no Etherscan budget.

Reads go through `cctp_feed.strict_post`, never `utils.hl_post`. The latter's
failure sentinel for `userVaultEquities` is `[]` — byte-identical to "deposits
into no vault" — so a timeout would serialise as a measured absence (rule 5).
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import hl_surface
from src.alerts import alert_cluster_referral, alert_cluster_subaccount
from src.cctp_feed import strict_post
from src.utils import DATA_DIR, load_config

MAX_WALLETS = 120
MAX_REFERRERS = 30
# Checked BETWEEN wallets. Nesting: one call's 30s timeout < this < the step's
# 6-minute timeout in trace.yml.
TIME_BUDGET_SECONDS = 240
PACE_SECONDS = 0.15


def strict_read(body: dict):
    return strict_post(body, timeout=30, retries=2)


def cluster(config: dict) -> list[str]:
    """Config only: target + known_self_wallets. A roster tier is an inference."""
    out = []
    for a in [config.get("target_wallet")] + list(config.get("known_self_wallets") or []):
        a = (a or "").strip().lower()
        if a and a not in out:
            out.append(a)
    return out


def wallets_to_check(config: dict, roster: dict) -> list[str]:
    from src.roster import detector_candidates

    out = cluster(config)
    for a in detector_candidates(config, roster, MAX_WALLETS):
        if a not in out:
            out.append(a)
    return out[:max(MAX_WALLETS, len(cluster(config)))]


def _ask(post, kind: str, wallet: str):
    """(payload, ok). A raised read is not an answer."""
    try:
        return post({"type": kind, "user": wallet}), True
    except Exception as exc:                          # noqa: BLE001 - transport
        print(f"[hl_surface] {wallet[:12]}... {kind} unreadable: {type(exc).__name__}: {exc}")
        return None, False


def read_wallet(wallet: str, post, sleep) -> tuple[dict, int]:
    """One wallet's three readings, each None when it could not be read."""
    unreadable = 0
    subs, ok = _ask(post, "subAccounts", wallet)
    subaccounts = hl_surface.parse_subaccounts(subs, wallet) if ok else None
    sleep(PACE_SECONDS)
    vaults_raw, ok = _ask(post, "userVaultEquities", wallet)
    vaults = hl_surface.parse_vaults(vaults_raw) if ok else None
    sleep(PACE_SECONDS)
    ref_raw, ok = _ask(post, "referral", wallet)
    ref = hl_surface.referral_reading(ref_raw) if ok else None
    sleep(PACE_SECONDS)
    for value in (subaccounts, vaults, ref):
        unreadable += value is None
    return {"subaccounts": subaccounts, "vaults": vaults, "referral": ref}, unreadable


def run(config: dict, roster: dict, previous: dict | None, post, *, now: datetime,
        budget_seconds: float, clock, sleep) -> dict:
    wallets = wallets_to_check(config, roster)
    start = clock()
    readings: dict[str, dict] = {}
    unreadable = 0
    not_reached: list[str] = []
    for i, wallet in enumerate(wallets):
        if clock() - start >= budget_seconds:
            not_reached = wallets[i:]
            break
        readings[wallet], bad = read_wallet(wallet, post, sleep)
        unreadable += bad

    sizes = dict((previous or {}).get("referrer_sizes") or {})
    for referrer in hl_surface.referrers_to_measure(readings, sizes, now, MAX_REFERRERS):
        # Same deadline: left unmeasured, a referrer simply casts no vote.
        if clock() - start >= budget_seconds:
            break
        payload, ok = _ask(post, "referral", referrer)
        reading = hl_surface.referral_reading(payload) if ok else None
        if reading is None:
            unreadable += 1
        else:
            sizes[referrer] = {"accounts": len(reading["referred"]),
                               "measured_at": now.isoformat()}
        sleep(PACE_SECONDS)

    shared = {(a or "").lower() for a in config.get("hl_shared_destinations") or []}
    report = hl_surface.build_report(readings, sizes, set(cluster(config)), shared)
    return {"computed_at": now.isoformat(), "cluster": cluster(config),
            "wallets_checked": len(readings), "unreadable": unreadable,
            "not_reached": not_reached, "readings": readings,
            "referrer_sizes": sizes, **report}


def fire_alerts(previous: dict | None, report: dict) -> list[dict]:
    """Alert on links that are new, or whose alert failed last run. Returns the
    links that are still undelivered, so they can be queued in the report."""
    retry = [x for x in (previous or {}).get("undelivered_alerts") or []
             if hl_surface.link_key(x) in {hl_surface.link_key(y)
                                           for y in report.get("links") or []}]
    pending, seen = [], set()
    for link in retry + hl_surface.new_links(previous, report):
        key = hl_surface.link_key(link)
        if key in seen:
            continue
        seen.add(key)
        if link["kind"] == "subaccount":
            ok = alert_cluster_subaccount(link, (report.get("subaccounts") or {})
                                          .get(link["address"]) or {})
        elif link["kind"] == "referral":
            ok = alert_cluster_referral(link)
        else:
            continue
        if not ok:
            pending.append(link)
    return pending


def _load(path: Path) -> dict:
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    config = load_config()
    roster = _load(DATA_DIR / "roster" / "latest.json")
    previous = _load(hl_surface.HL_SURFACE_DIR / "latest.json") or None
    report = run(config, roster, previous, strict_read, now=datetime.now(UTC),
                 budget_seconds=TIME_BUDGET_SECONDS, clock=time.monotonic,
                 sleep=time.sleep)

    kinds: dict[str, int] = {}
    for link in report["links"]:
        kinds[link["kind"]] = kinds.get(link["kind"], 0) + 1
    print(f"[hl_surface] checked {report['wallets_checked']} wallet(s), "
          f"{report['unreadable']} unreadable read(s), "
          f"{len(report['not_reached'])} not reached in the time budget")
    print(f"[hl_surface] {len(report['subaccounts'])} sub-account(s), "
          f"{len(report['referrals'])} referred wallet(s), "
          f"{len(report['referrer_sizes'])} referrer(s) measured, "
          f"{len(report['vault_deposits'])} wallet(s) in non-shared vaults")
    print(f"[hl_surface] links: {kinds or 'none'}")
    for link in report["links"]:
        if link["kind"] != "referral_pair":
            print(f"[hl_surface]   {link['kind']}: {link['address']} -> {link['linked_to']}")
    report["undelivered_alerts"] = fire_alerts(previous, report)
    hl_surface.save(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
