"""Who does treasury work when he does — co-occurrence of non-trading actions.

Co-firing (`src/cofire.py`) asked who opens his BURSTS with him and answered no
over all 294 sessions: whatever other wallet he runs is not traded in the same
seconds. This asks the other half of the habit. A person running two accounts
tops up, withdraws, re-approves agents and moves between spot and perp in one
sitting — his FTX screenshots show him viewing "all subaccounts" at once — and
those actions are venue-wide, timestamped to the block and keyless in SQD's
replica-command feed (`src/replica_cmds.py`), with none of the custody gap that
hides a funding link.

Shape, deliberately the same as co-firing's:

* **His sessions** are the TARGET's own non-trading actions (plus his ledger
  deposits, which are L1 events the action feed does not carry) clustered by a
  10-minute gap. His known wallets are NOT folded in as "him": they are measured
  like strangers, so they are **positive controls** — the vector is validated
  only if it finds them.
* **Per session** every account with a treasury-kind action inside the padded
  window counts once — and only for actions that do not touch him. A wallet he
  paid in that session, or whose action paid a cluster wallet, is a transfer the
  ledger already shows; this measures acting at the same time WITHOUT touching
  him. An account with more than `BOT_ACTIONS` actions in one window is a bot
  for that window.
* **Chance** is each account's own base rate from control windows: the same
  span shifted by ±7 days. `lift` = session rate / control rate (0.5 smoothed);
  `p` is the binomial tail of being present in ≥ s of n sessions at the control
  rate.
* **A hit** needs ≥ `MIN_SESSIONS` sessions, lift ≥ `MIN_LIFT` and p ≤ `MAX_P` (1e-4:
  thousands of accounts are tested, ~400 are present in any 20-minute window).
  Hits register as candidates; nothing here mints a vote (rule 4: there is no
  hit to calibrate an LR against until one of his known wallets reads as one).

Pure functions; the script owns the network and the files.
"""

from __future__ import annotations

import math
from collections import defaultdict

from src import replica_cmds as rc

SESSION_GAP_MS = 10 * 60_000
PAD_MS = 10 * 60_000
CONTROL_SHIFTS_MS = (-7 * 86_400_000, 7 * 86_400_000)
BOT_ACTIONS = 20
MIN_SESSIONS = 4
MIN_LIFT = 5.0
MAX_P = 1e-4
MAX_WALLETS = 20_000
MAX_SESSION_KEYS = 24
WINDOW_TYPES = rc.TREASURY_TYPES + ("approveAgent",)
# Ledger kinds that are his own act (a deposit is him paying the bridge on L1).
LEDGER_KINDS = ("deposit", "withdraw", "accountClassTransfer", "send", "spotTransfer",
                "internalTransfer", "subAccountTransfer", "vaultDeposit", "vaultWithdraw")
DESTINATION_KEYS = ("destination", "destinationRecipient", "subAccountUser", "vaultAddress",
                    "agentAddress")


def _low(value) -> str:
    return str(value).lower() if isinstance(value, str) else ""


def target_events(actions: list[dict], ledger_rows: list[dict], start_ms: int) -> list[dict]:
    """His own treasury acts since `start_ms`, as {t, type, destination}."""
    out = []
    for a in actions or []:
        t = a.get("time")
        if not isinstance(t, int) or t < start_ms:
            continue
        kind = a.get("type")
        if kind not in rc.NON_TRADING_TYPES:
            continue
        out.append({"t": t, "type": kind, "destination": _low(a.get("destination")) or None})
    for r in ledger_rows or []:
        t = r.get("time")
        delta = r.get("delta") if isinstance(r.get("delta"), dict) else {}
        kind = delta.get("type")
        if not isinstance(t, int) or t < start_ms or kind not in LEDGER_KINDS:
            continue
        dest = _low(delta.get("destination") or delta.get("user"))
        out.append({"t": t, "type": f"ledger:{kind}", "destination": dest or None})
    out.sort(key=lambda e: e["t"])
    return out


def sessions(events: list[dict], gap_ms: int = SESSION_GAP_MS) -> list[dict]:
    """Events clustered by a gap. Each: {key, start, end, n, kinds, destinations}."""
    out: list[dict] = []
    for e in sorted(events or [], key=lambda e: e["t"]):
        if out and e["t"] - out[-1]["end"] <= gap_ms:
            s = out[-1]
            s["end"] = e["t"]
            s["n"] += 1
            s["kinds"][e["type"]] = s["kinds"].get(e["type"], 0) + 1
            if e.get("destination"):
                s["destinations"].add(e["destination"])
        else:
            out.append({"key": str(e["t"]), "start": e["t"], "end": e["t"], "n": 1,
                        "kinds": {e["type"]: 1},
                        "destinations": {e["destination"]} if e.get("destination") else set()})
    return out


def window(session: dict, pad_ms: int = PAD_MS) -> tuple[int, int]:
    return int(session["start"]) - pad_ms, int(session["end"]) + pad_ms


def control_windows(session: dict, dataset_start_ms: int, head_ms: int,
                    shifts=CONTROL_SHIFTS_MS, pad_ms: int = PAD_MS) -> list[tuple[int, int]]:
    """The same span on other days, clipped to what the dataset holds."""
    a, b = window(session, pad_ms)
    out = []
    for shift in shifts:
        lo, hi = a + shift, b + shift
        if lo >= dataset_start_ms and hi <= head_ms:
            out.append((lo, hi))
    return out


def action_destination(row: dict) -> str:
    payload = row.get("action") or {}
    for key in DESTINATION_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("0x"):
            return value.lower()
    return ""


def present_users(rows: list[dict], cluster: set, *, exclude: set | None = None,
                  bot_actions: int = BOT_ACTIONS) -> dict[str, dict]:
    """Accounts with at least one qualifying action in a window, with what they did.

    Qualifying: status ok, kind in WINDOW_TYPES, user not in the cluster, not in
    `exclude` (the wallets he paid in this session), and the action's destination
    not a cluster wallet. A user with more than `bot_actions` actions in the
    window is dropped for the window.
    """
    cluster = {_low(c) for c in cluster}
    exclude = {_low(x) for x in (exclude or set())}
    counts: dict[str, int] = defaultdict(int)
    kinds: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for row in rows or []:
        user = _low(row.get("user"))
        if not user or user in cluster or user in exclude:
            continue
        if row.get("status") not in (None, "ok") or row.get("type") not in WINDOW_TYPES:
            continue
        if action_destination(row) in cluster:
            continue
        counts[user] += 1
        kinds[user][row["type"]] += 1
    return {u: {"n": n, "kinds": dict(kinds[u])} for u, n in counts.items() if n <= bot_actions}


def empty_state() -> dict:
    return {"version": 1, "sessions": 0, "controls": 0, "done": {}, "wallets": {}}


def fold_session(state: dict, session: dict, rows: list[dict], control_rows: list[list[dict]],
                 cluster: set, now_ms: int) -> dict:
    """Fold one session's read into `state` (mutates). Returns the contribution."""
    if session["key"] in state["done"]:
        return {"skipped": True}
    present = present_users(rows, cluster, exclude=session.get("destinations") or set())
    wallets = state.setdefault("wallets", {})
    for user, what in present.items():
        entry = wallets.get(user)
        if entry is None:
            entry = wallets[user] = {"s": 0, "c": 0, "k": {}, "ss": [], "f": now_ms, "l": now_ms}
        entry["s"] += 1
        entry["l"] = now_ms
        for kind, n in what["kinds"].items():
            entry["k"][kind] = int(entry["k"].get(kind) or 0) + int(n)
        if len(entry["ss"]) < MAX_SESSION_KEYS:
            entry["ss"].append(session["key"])
    control_present: set = set()
    for crow in control_rows:
        for user in present_users(crow, cluster):
            entry = wallets.get(user)
            if entry is None:
                entry = wallets[user] = {"s": 0, "c": 0, "k": {}, "ss": [], "f": now_ms, "l": now_ms}
            entry["c"] += 1
            control_present.add(user)
    state["sessions"] += 1
    state["controls"] += len(control_rows)
    state["done"][session["key"]] = {"start": session["start"], "end": session["end"],
                                     "n": session["n"], "present": len(present),
                                     "controls": len(control_rows),
                                     "control_present": len(control_present)}
    return {"present": len(present), "controls": len(control_rows),
            "control_present": len(control_present)}


def control_rate(entry: dict, controls: int) -> float:
    return (float(entry.get("c") or 0) + 0.5) / (float(controls) + 1.0)


def lift(entry: dict, sessions_n: int, controls: int) -> float | None:
    if sessions_n <= 0 or controls <= 0:
        return None
    rate = float(entry.get("s") or 0) / float(sessions_n)
    return rate / control_rate(entry, controls)


def binomial_tail(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p), in logs for stability."""
    if k <= 0:
        return 1.0
    if k > n or p <= 0:
        return 0.0 if k > n else (0.0 if p <= 0 else 1.0)
    if p >= 1:
        return 1.0
    total = 0.0
    lp, lq = math.log(p), math.log(1 - p)
    for i in range(k, n + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * lp + (n - i) * lq)
    return min(1.0, total)


def p_value(entry: dict, sessions_n: int, controls: int) -> float | None:
    if sessions_n <= 0 or controls <= 0:
        return None
    return binomial_tail(int(entry.get("s") or 0), int(sessions_n), control_rate(entry, controls))


def is_hit(entry: dict, sessions_n: int, controls: int) -> bool:
    if int(entry.get("s") or 0) < MIN_SESSIONS:
        return False
    lf = lift(entry, sessions_n, controls)
    pv = p_value(entry, sessions_n, controls)
    return lf is not None and pv is not None and lf >= MIN_LIFT and pv <= MAX_P


def screen(state: dict, *, exclude: set | None = None, limit: int = 50) -> list[dict]:
    """Ranked accounts: most sessions with him first, then the lowest p."""
    exclude = {_low(x) for x in (exclude or set())}
    n, c = int(state.get("sessions") or 0), int(state.get("controls") or 0)
    rows = []
    for wallet, entry in (state.get("wallets") or {}).items():
        if wallet in exclude or int(entry.get("s") or 0) <= 0:
            continue
        lf, pv = lift(entry, n, c), p_value(entry, n, c)
        rows.append({"wallet": wallet, "sessions_with": int(entry.get("s") or 0),
                     "controls_with": int(entry.get("c") or 0), "sessions": n, "controls": c,
                     "lift": None if lf is None else round(lf, 2),
                     "p": None if pv is None else float(f"{pv:.3g}"),
                     "kinds": dict(entry.get("k") or {}), "session_keys": list(entry.get("ss") or []),
                     "hit": is_hit(entry, n, c)})
    rows.sort(key=lambda r: (-r["sessions_with"], r["p"] if r["p"] is not None else 1.0))
    return rows[:limit]


def cap(state: dict, limit: int = MAX_WALLETS) -> dict:
    """Keep the accounts with the most sessions; a tie keeps the newest."""
    wallets = state.get("wallets") or {}
    if len(wallets) <= limit:
        return state
    keep = sorted(wallets.items(), key=lambda kv: (int(kv[1].get("s") or 0), int(kv[1].get("l") or 0)),
                  reverse=True)[:limit]
    state["wallets"] = dict(keep)
    return state


def summary(state: dict, hits: list[dict], controls_known: dict | None = None) -> dict:
    wallets = state.get("wallets") or {}
    return {"sessions": int(state.get("sessions") or 0), "controls": int(state.get("controls") or 0),
            "wallets_seen": len(wallets),
            "wallets_with_2plus": sum(1 for e in wallets.values() if int(e.get("s") or 0) >= 2),
            "hits": len(hits), "known_wallets": controls_known or {}}
