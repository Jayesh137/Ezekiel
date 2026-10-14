"""The operator's decision queue: questions only a human can settle, made visible.

Two of them sat unseen for weeks — `0x160f6ef9…` (money both ways with him,
not a Hyperliquid account: if it is his, `0x793a3e8a…` reaches PROBABLE by
itself) and `0xb83de012…` (the referral twins' referrer, unwatched since
2026-09-16) — because nothing in the pipeline could act on them and nothing
told the operator they were waiting. A ruling is ground truth
(`config.casebook_rulings`, `config.known_self_wallets`), so the system must
never make it; what it can do is keep the list short, ranked, and one command
from settled.

Pure. `scripts/send_digest.py` writes `data/decisions/latest.json` every run
and the digest carries the top of the queue; `scripts/casebook.py rule`
records the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

MAX_PENDING = 5
# A casebook case worth a human look: below this the queue would never end.
MIN_P_NOW = 0.003
# A watch question left open this long is a decision, not an observation.
STALE_QUESTION_DAYS = 30


def _addr(value) -> str:
    return str(value or "").strip().lower()


def _rulings(config: dict) -> dict:
    return {_addr(k): v for k, v in (config.get("casebook_rulings") or {}).items()}


def _known(config: dict) -> set:
    out = {_addr(w) for w in (config.get("known_self_wallets") or []) if isinstance(w, str)}
    out.add(_addr(config.get("target_wallet")))
    out.discard("")
    return out


def command(address: str, verdict: str = "him|not_him|unsure") -> str:
    return f'python scripts/casebook.py rule {address} {verdict} --note "..."'


def from_casebook(casebook: dict | None, config: dict, *, min_p: float = MIN_P_NOW) -> list[dict]:
    """Unknown cases ranked high enough to deserve a human reading, unruled."""
    rulings, known = _rulings(config), _known(config)
    out = []
    for case in (casebook or {}).get("cases") or []:
        if not isinstance(case, dict) or case.get("known") or case.get("excluded"):
            continue
        address = _addr(case.get("address"))
        if not address or address in known or address in rulings or case.get("ruling"):
            continue
        p = case.get("p_now")
        if not isinstance(p, (int, float)) or p < min_p:
            continue
        hl = case.get("hl") if isinstance(case.get("hl"), dict) else {}
        out.append({"address": address, "kind": "suspect", "p_now": round(float(p), 5),
                    "on_hl": bool(hl.get("on_hl")), "value": hl.get("value"),
                    "why": str(case.get("headline") or "")[:160],
                    "ask": ("Not a Hyperliquid account: only you can say whether it is his — "
                            "if it is, every wallet it funds inherits the link."
                            if not hl.get("on_hl") else
                            "On Hyperliquid: rule it, or leave it to the detectors."),
                    "command": command(address)})
    return out


def from_watch(config: dict, now: datetime, *, stale_days: int = STALE_QUESTION_DAYS) -> list[dict]:
    """`watch_wallets` questions the operator opened and has not closed."""
    rulings, known = _rulings(config), _known(config)
    out = []
    for entry in config.get("watch_wallets") or []:
        if isinstance(entry, dict):
            address, why = _addr(entry.get("address")), entry.get("why")
            since = entry.get("since") or entry.get("added")
        else:
            address, why, since = _addr(entry), None, None
        if not address or address in known or address in rulings:
            continue
        age = None
        if isinstance(since, str):
            try:
                opened = datetime.fromisoformat(since.replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=UTC)
                age = (now - opened).days
            except ValueError:
                age = None
        if age is not None and age < stale_days:
            continue
        out.append({"address": address, "kind": "watch_question", "age_days": age,
                    "why": str(why or "under close watch as a question")[:160],
                    "ask": "Watched as a question; the watch cannot close it. Rule it or drop it.",
                    "command": command(address)})
    return out


def pending(config: dict, casebook: dict | None, now: datetime | None = None,
            *, limit: int = MAX_PENDING) -> dict:
    """The queue, ranked: suspects by p_now, then stale watch questions."""
    now = now or datetime.now(UTC)
    suspects = sorted(from_casebook(casebook, config), key=lambda d: -d["p_now"])
    questions = from_watch(config, now)
    items = (suspects + questions)[:limit]
    return {"computed_at": now.isoformat(), "pending": items,
            "counts": {"suspects": len(suspects), "watch_questions": len(questions)},
            "ruled": len(_rulings(config))}


def render_line(queue: dict, short) -> str | None:
    """One digest line, or None when nothing waits."""
    items = (queue or {}).get("pending") or []
    if not items:
        return None
    parts = []
    for item in items[:3]:
        if item["kind"] == "suspect":
            where = "HL" if item.get("on_hl") else "not on HL"
            parts.append(f"{short(item['address'])} {100 * item['p_now']:.2f}% ({where})")
        else:
            parts.append(f"{short(item['address'])} watched {item.get('age_days') or '?'}d")
    more = len(items) - min(3, len(items))
    return ("Needs your ruling: " + "; ".join(parts)
            + (f"; +{more} more" if more > 0 else "")
            + " — `python scripts/casebook.py rule <address> him|not_him|unsure`.")
