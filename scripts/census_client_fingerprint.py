#!/usr/bin/env python3
"""Census of trading clients over the venue's takers, one `historicalOrders` read each.

Population: the venue sample (`data/tape/venue_population.json`), the tape
sketches and the registry; program-shaped takers first. Profiles in
`data/client_census/profiles.json`, report in `data/client_census/latest.json`:
the base rate of his client class over everything measured, the wallets in his
class (registered as `client_census` for the execution-program detector), his
known wallets' classes and a split-half reading of the target as controls.
Bounded by `src.hl_budget.ReadBudget`; resumable. No vote, no alert.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import client_census as cc  # noqa: E402
from src.hl_budget import ReadBudget  # noqa: E402
from src.utils import DATA_DIR, atomic_write_json, hl_read, load_config  # noqa: E402

OUT_DIR = DATA_DIR / "client_census"
PROFILES_FILE = OUT_DIR / "profiles.json"
REPORT_FILE = OUT_DIR / "latest.json"
DEFAULT_TIME_BUDGET = 150
DEFAULT_MAX_WALLETS = 40
WEIGHT_PER_MINUTE = 600


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_profiles() -> dict:
    doc = _json(PROFILES_FILE)
    return doc if isinstance(doc, dict) else {}


def registry_wallets() -> list[str]:
    from src.candidate_registry import iter_candidates
    rows = iter_candidates(DATA_DIR)
    rows.sort(key=lambda r: str(r.get("last_positive_evidence") or ""), reverse=True)
    return [str(r.get("wallet") or "").lower() for r in rows if r.get("wallet")]


def read_orders(wallet: str, fetch=hl_read) -> tuple[list | None, str | None]:
    got = fetch({"type": "historicalOrders", "user": wallet})
    if not isinstance(got, dict) or not got.get("ok") or not isinstance(got.get("data"), list):
        return None, (got or {}).get("error") if isinstance(got, dict) else "no answer"
    return got["data"], None


def census(wallets: list[str], profiles: dict, now_ms: int, *, fetch=hl_read, budget=None,
           log=print) -> dict:
    """Read each wallet once; store a compact profile. Returns what the run did."""
    read, failed, his = [], [], []
    for wallet in wallets:
        if budget is not None and not budget.can_continue():
            break
        rows, error = read_orders(wallet, fetch)
        if rows is None:
            failed.append({"wallet": wallet, "error": str(error)[:120]})
            continue
        prof = cc.profile(rows)
        profiles[wallet] = cc.compact(prof, now_ms)
        read.append(wallet)
        if cc.his_class(prof):
            his.append(wallet)
            log(f"[client] HIS CLASS {wallet} ioc={prof['ioc_nocloid_share']} runs={prof['ioc_runs']} "
                f"orders={prof['orders']} coins={prof['coins']}")
    return {"read": read, "failed": failed, "his_class": his}


def controls(config: dict, profiles: dict, now_ms: int, *, fetch=hl_read, budget=None) -> dict:
    """His known wallets' classes and the target's split-half, re-read every RECHECK_MS."""
    target = str(config.get("target_wallet") or "").lower()
    known = [str(w).lower() for w in config.get("known_self_wallets") or []]
    out = {"known": {}, "target_split_half": None}
    for w in known:
        if budget is not None and not budget.can_continue():
            break
        if not cc.due(profiles, w, now_ms):
            out["known"][w] = profiles[w]
            continue
        rows, error = read_orders(w, fetch)
        if rows is None:
            out["known"][w] = {"error": str(error)[:120]}
            continue
        profiles[w] = cc.compact(cc.profile(rows), now_ms)
        out["known"][w] = profiles[w]
    if budget is None or budget.can_continue():
        rows, error = read_orders(target, fetch)
        if rows is not None:
            out["target_split_half"] = cc.split_half(rows)
            profiles[target] = cc.compact(cc.profile(rows), now_ms)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET)
    parser.add_argument("--max-wallets", type=int, default=DEFAULT_MAX_WALLETS)
    args = parser.parse_args()
    config = load_config()
    cluster = {str(config.get("target_wallet") or "").lower()}
    cluster |= {str(w).lower() for w in config.get("known_self_wallets") or []}
    profiles = load_profiles()
    population = _json(DATA_DIR / "tape" / "venue_population.json") or {}
    sketches = _json(DATA_DIR / "tape" / "sketches.json") or {}
    sketch_wallets = sketches.get("wallets") if isinstance(sketches.get("wallets"), dict) else sketches
    now_ms = int(time.time() * 1000)
    wallets = [w for w in cc.pick_wallets(population, sketch_wallets if isinstance(sketch_wallets, dict) else None,
                                          registry_wallets(), profiles, now_ms, args.max_wallets)
               if w not in cluster]
    previous = _json(REPORT_FILE) or {}
    with ReadBudget(seconds=args.time_budget, weight_per_minute=WEIGHT_PER_MINUTE) as budget:
        ctl = controls(config, profiles, now_ms, budget=budget)
        run = census(wallets, profiles, now_ms, budget=budget)
        budget_report = budget.report()
    profiles = cc.cap(profiles)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(PROFILES_FILE, profiles, sort_keys=True, compact=True)

    his_all = sorted((w for w, e in profiles.items() if e.get("his") and w not in cluster),
                     key=lambda w: -int(profiles[w].get("t") or 0))
    from src.candidate_registry import observe_candidate
    for w in run["his_class"]:
        observe_candidate(w, {"source": "client_census", "positive": True, "event_id": f"client:{w}",
                              "detail": {k: profiles[w].get(k) for k in ("ioc", "runs", "n", "sd", "lab")}})
    rate = cc.base_rate({w: e for w, e in profiles.items() if w not in cluster})
    report = {"computed_at": datetime.now(UTC).isoformat(), "base_rate": rate,
              "his_class_wallets": [{"wallet": w, **profiles[w]} for w in his_all[:50]],
              "controls": ctl if ctl.get("target_split_half") is not None or ctl.get("known") else previous.get("controls"),
              "run": {"candidates": len(wallets), "read": len(run["read"]), "failed": run["failed"][:10],
                      "his_class_new": run["his_class"], "budget": budget_report},
              "population_pending": sum(1 for w in (population.get("wallets") or {})
                                        if cc.due(profiles, w, now_ms))}
    atomic_write_json(REPORT_FILE, report)
    print(f"[client] read {len(run['read'])}/{len(wallets)} ({len(run['failed'])} failed), "
          f"{len(run['his_class'])} new in his class; base rate {rate['his_class']}/{rate['measured']} "
          f"measured (upper {rate['upper']}), labels {rate['labels']}; {report['population_pending']} pending")
    for w, e in (ctl.get("known") or {}).items():
        print(f"  known {w[:12]} class={e.get('lab')} ioc={e.get('ioc')} fe={e.get('fe')} n={e.get('n')}")
    sh = ctl.get("target_split_half") or {}
    print(f"  target split-half: {sh.get('agree')} older={((sh.get('older') or {}).get('lab'))} "
          f"newer={((sh.get('newer') or {}).get('lab'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
