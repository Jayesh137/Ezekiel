#!/usr/bin/env python3
"""Measure how rare the target's execution program is across the venue.

The execution-program vector only votes once its false-positive rate is MEASURED
(CLAUDE.md rule 4). This walks the leaderboard, builds each account's signature,
compares it to the target's clip table, and writes the distribution of match
ratios to `data/execution_program/census.json`. `is_discriminating` then reads the
99th-percentile ratio as the bar a candidate must beat.

It is resumable: processed addresses and their ratios persist in the committed
`data/execution_program/census_state.json`, so successive Actions runs walk deeper
into the population instead of re-measuring the same accounts. Any account that
itself reproduces the table (a real lead) is recorded in the output regardless of
the threshold.

Population: leaderboard accounts with real size that traded recently, largest week
volume first, skipping the extreme-volume market makers whose newest 2,000 fills
span only minutes (their fill window is too short to carry a program).
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

from src import execution_program as ep
from src.utils import DATA_DIR, atomic_write_json, load_config

OUT_DIR = DATA_DIR / "execution_program"
CENSUS_FILE = OUT_DIR / "census.json"
# Committed (not data/.local), so the measured population ACCUMULATES across runs
# even on ephemeral GitHub Actions runners — otherwise each daily run would
# re-measure the same top-volume accounts and the threshold could never build, and
# the vector could never vote until the VM existed. Capped so it cannot grow the
# repo without bound.
STATE = OUT_DIR / "census_state.json"
MAX_STATE_ROWS = 20_000
# A wallet reproducing this many of his exact per-coin clip sizes is a lead worth
# recording in the output whatever the population distribution turns out to be.
HIT_MIN_CLIPS = 4
MIN_ACCOUNT_VALUE = 250_000
MIN_WEEK_VOLUME = 100_000
MAX_WEEK_VOLUME = 2_000_000_000


def window_volume(row, window):
    for name, perf in row.get("windowPerformances", []):
        if name == window:
            try:
                return float(perf.get("vlm") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def population(leaderboard, exclude):
    """Eligible accounts in a stable, uniform (hash-ordered) sample.

    The census measures how often a RANDOM venue account reproduces his clip
    sizes, so a uniform sample of the eligible band is the right negative
    population. It also reaches clip-style traders far sooner than a volume-desc
    walk, which front-loads the market makers that carry no clip table at all
    (verified 2026-09-29: 0 of the top 26 by volume were measurable) — and the
    threshold, hence the vote, can only build once measured accounts accumulate.
    Deterministic so a resumed sweep covers everyone exactly once.
    """
    import hashlib
    rows = [r for r in leaderboard
            if float(r.get("accountValue") or 0) >= MIN_ACCOUNT_VALUE
            and MIN_WEEK_VOLUME <= window_volume(r, "week") <= MAX_WEEK_VOLUME
            and (r.get("ethAddress") or "").lower() not in exclude]
    return sorted((r["ethAddress"].lower() for r in rows if r.get("ethAddress")),
                  key=lambda a: hashlib.sha256(a.encode()).hexdigest())


def fetch_leaderboard():
    url = load_config()["leaderboard_url"]
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.json().get("leaderboardRows", [])


def load_state():
    try:
        with open(STATE) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"processed": {}, "hits": {}}


def target_signature():
    from src.fingerprint import load_fills, load_orders
    return ep.signature(load_fills(), load_orders())


def cap_state(state):
    """Keep the state bounded: the newest MAX_STATE_ROWS processed rows, all hits.

    Hits are few and precious (accounts reproducing his table), so they are never
    evicted; ordinary measured/insufficient rows are trimmed oldest-first by their
    observation time so the committed file cannot grow the repo without bound.
    """
    processed = state.get("processed", {})
    if len(processed) > MAX_STATE_ROWS:
        keep = sorted(processed.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_STATE_ROWS:]
        processed = dict(keep)
    return {"processed": processed, "hits": state.get("hits", {})}


def register_hits(hits, data_dir=None):
    """Make each account reproducing his clip table an investigation candidate.

    A census hit trades like his script but may have no financial link yet. Adding
    it to the registry (a behaviour observation, never a financial fact) lets the
    trace detectors examine it for an independent vector next run — turning
    "trades like his program" into a full investigation venue-wide, not just for
    accounts already on the roster. The execution VOTE still comes only from the
    census-gated detector, so this cannot mint a tier by itself.
    """
    from src.candidate_registry import observe_candidate, valid_wallet
    registered = []
    for addr, hit in (hits or {}).items():
        try:
            wallet = valid_wallet(addr)
        except ValueError:
            continue
        observe_candidate(wallet, {
            "source": "execution_program_census", "positive": True,
            "event_id": f"execprog:{wallet}",
            "detail": {k: hit.get(k) for k in ("clips_matched", "clip_match_ratio")},
        }, data_dir=data_dir)
        registered.append(wallet)
    return registered


def write_census(state):
    ratios = [v["ratio"] for v in state["processed"].values() if v.get("ratio") is not None]
    rhos = [v["rho"] for v in state["processed"].values() if v.get("rho") is not None]
    census = ep.summarise_census(ratios, rhos)
    census.update(computed_at=datetime.now(UTC).isoformat(),
                  measured=len(ratios), attempted=len(state["processed"]),
                  hits=sorted(state["hits"].values(), key=lambda h: -h.get("clips_matched", 0))[:50])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(CENSUS_FILE, census)
    return census


def run(*, limit, budget_seconds, fetch=None, leaderboard=None):
    from src.hl_budget import ReadBudget
    from src.utils import hl_read

    fetch = fetch or hl_read
    config = load_config()
    target = config.get("target_wallet", "").lower()
    exclude = {target, *[w.lower() for w in config.get("known_self_wallets", [])]}
    target_sig = target_signature()
    if not target_sig.get("clip_table"):
        raise SystemExit("no target clip table; collect fills first")
    leaderboard = leaderboard if leaderboard is not None else fetch_leaderboard()
    state = load_state()
    todo = [a for a in population(leaderboard, exclude) if a not in state["processed"]]
    processed_now = 0
    with ReadBudget(seconds=budget_seconds, weight_per_minute=600) as read_budget:
        for addr in todo[:limit]:
            if not read_budget.can_continue():
                break
            result = fetch({"type": "userFills", "user": addr})
            if not result.get("ok"):
                continue
            sig = ep.signature(result["data"])
            match = ep.compare(target_sig, sig)
            measured = match["status"] == "measured"
            ratio = match["clip_match_ratio"] if measured else None
            rho = match.get("notional_structure_rho") if measured else None
            state["processed"][addr] = {"ratio": ratio, "rho": rho,
                                        "clips_compared": match.get("clips_compared"),
                                        "at": int(time.time())}
            # A hit is a strong reproduction of his program by EITHER path: his
            # exact clip sizes, or (rescale-robust) his per-coin rank structure.
            strong_structure = (match.get("notional_structure_rho") or 0) >= 0.9 \
                and (match.get("notional_coins_compared") or 0) >= ep.MIN_STRUCTURE_COINS
            if (match.get("clips_matched") or 0) >= HIT_MIN_CLIPS or strong_structure:
                state["hits"][addr] = {"wallet": addr, **{k: match.get(k) for k in
                    ("clip_match_ratio", "clips_matched", "clips_compared",
                     "notional_structure_rho", "notional_coins_compared",
                     "cadence_agreement", "offset_agreement")}}
            processed_now += 1
    state = cap_state(state)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(STATE, state)
    register_hits(state["hits"])
    census = write_census(state)
    print(f"[census] +{processed_now} this run; population measured "
          f"{census['measured']}/{census['attempted']}; ratio_p99={census['ratio_p99']}; "
          f"ratio_max={census['ratio_max']}; hits={len(state['hits'])}")
    return census


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=400, help="max new accounts this run")
    parser.add_argument("--budget-seconds", type=int, default=1800)
    args = parser.parse_args()
    run(limit=args.limit, budget_seconds=args.budget_seconds)


if __name__ == "__main__":
    main()
