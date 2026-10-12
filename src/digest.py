"""The daily digest: one quiet message a day so silence has a meaning.

Only CRITICAL and HIGH reach the phone, and that is right — the first trace run
after ntfy went live pushed 24 notices in a minute. But it leaves the operator
unable to tell a quiet day from a dead system, or to see a lead creeping up the
casebook without being paged for it. One low-priority message at a fixed hour
answers the three questions a copy-trader has every morning:

  * Is he still trading from the wallet I follow, and is his silence normal?
  * Which of his wallets are live — the set the owner should be following today?
  * Did anything move — new suspects, a vote gate opening, a watched wallet?
  * Is the system alive — feeds fresh, alerts delivering, data current?

Pure functions over the committed readings; `scripts/send_digest.py` loads the
files, decides whether today's digest is due and sends it once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src import execution_program as ep

DIGEST_HOUR_UTC = 7
TOP_SUSPECTS = 3
WORLD_TIERS = ("CONFIRMED", "PROBABLE")


def _short(address: str | None) -> str:
    address = str(address or "")
    return f"{address[:10]}…" if len(address) > 12 else address


def _money(value) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(value) >= 1e9:
        return f"${value / 1e9:.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:.1f}M"
    if abs(value) >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:.0f}"


def _age(stamp, now: datetime) -> str:
    """Human age of an ISO timestamp or epoch ms; 'unknown' when unreadable."""
    when = None
    if isinstance(stamp, (int, float)) and stamp > 0:
        when = datetime.fromtimestamp(float(stamp) / 1000, tz=UTC)
    elif isinstance(stamp, str) and stamp:
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        except ValueError:
            when = None
    if when is None:
        return "unknown"
    minutes = max(0, int((now - when).total_seconds() // 60))
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes / 60
    if hours < 36:
        return f"{hours:.0f}h ago"
    return f"{hours / 24:.1f}d ago"


def due(last_sent_day: str | None, now: datetime, hour_utc: int = DIGEST_HOUR_UTC) -> bool:
    """Once a day, from `hour_utc` on. A day already sent is never sent twice."""
    if now.hour < hour_utc:
        return False
    return last_sent_day != now.strftime("%Y-%m-%d")


def target_section(readings: dict, now: datetime) -> dict:
    dormancy = ((readings.get("dormancy") or {}).get("dormancy")) or {}
    stats = dormancy.get("stats") or {}
    account = readings.get("account_value") or {}
    return {
        "last_fill": _age(readings.get("fills_newest_ms"), now),
        "silent_days": dormancy.get("silent_days"),
        "p90": stats.get("p90"),
        "max_gap": stats.get("max"),
        "unusual": bool(dormancy.get("unusual")),
        "unprecedented": bool(dormancy.get("unprecedented")),
        "value": account.get("total"),
    }


def hunt_section(readings: dict, config: dict | None) -> dict:
    config = config or {}
    known = {str(w).lower() for w in (config.get("known_self_wallets") or []) if isinstance(w, str)}
    known.add(str(config.get("target_wallet") or "").lower())
    roster = readings.get("roster") or {}
    rows = roster.get("wallets") if isinstance(roster.get("wallets"), list) else []
    counts = {"CONFIRMED": 0, "PROBABLE": 0, "POSSIBLE": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tier = row.get("tier")
        if tier in counts and str(row.get("wallet") or "").lower() not in known:
            counts[tier] += 1
    casebook = readings.get("casebook") or {}
    cases = casebook.get("cases") if isinstance(casebook.get("cases"), list) else []
    suspects = []
    for case in cases:
        if not isinstance(case, dict) or case.get("known") or case.get("excluded"):
            continue
        hl = case.get("hl") or {}
        suspects.append({"address": case.get("address"), "p_now": case.get("p_now"),
                         "on_hl": hl.get("on_hl"), "value": hl.get("value"),
                         "headline": str(case.get("headline") or "")[:70]})
        if len(suspects) >= TOP_SUSPECTS:
            break
    watch = readings.get("watchlist") or {}
    watched = watch.get("wallets") if isinstance(watch.get("wallets"), list) else []
    return {"tiers": counts, "suspects": suspects, "watched": len(watched),
            "his_wallets": his_wallets(watched, known),
            "handoffs": len(((readings.get("dormancy") or {}).get("handoffs")) or {})}


def his_wallets(watched: list, known: set) -> list[dict]:
    """The set beyond the target that the owner should be following: his known
    wallets (config) and every roster-CONFIRMED wallet, with what each is doing.

    Read off the close watch, which reads them every run. A wallet under watch as
    a QUESTION (`config.watch_wallets`, source `config` but not known) is not his
    until a tier says so, and is left out.
    """
    out = []
    for row in watched:
        if not isinstance(row, dict):
            continue
        address = str(row.get("address") or "").lower()
        why = str(row.get("why") or "")
        confirmed = row.get("source") == "roster" and why.startswith("roster CONFIRMED")
        if not address or not (address in known or confirmed):
            continue
        out.append({"address": address, "value": row.get("account_value"),
                    "last_fill_ms": row.get("last_fill_ms"),
                    "how": "known" if address in known else "confirmed"})
    out.sort(key=lambda r: -(r.get("last_fill_ms") or 0))
    return out


def execution_section(readings: dict) -> dict:
    census = ep.merge_census(readings.get("census"), readings.get("tape_census"))
    probe = {"clip_match_ratio": 1.0, "clips_compared": 9, "clips_matched": 9}
    gate_open = bool(census and ep.is_discriminating(probe, census))
    report = readings.get("execution") or {}
    matches = report.get("matches") if isinstance(report.get("matches"), list) else []
    tape = readings.get("tape") or {}
    tape_census = readings.get("tape_census") or {}
    return {
        "gate_open": gate_open,
        "ceiling": (census or {}).get("comparable_upper"),
        "attempted": (census or {}).get("attempted"),
        "comparable": (readings.get("census") or {}).get("population"),
        "discriminating": sum(1 for m in matches if isinstance(m, dict) and m.get("discriminating")),
        "read": report.get("wallets_checked"),
        "candidates": report.get("candidates"),
        "sketch_wallets": tape_census.get("sketch_wallets"),
        "tape_ceiling": tape_census.get("comparable_upper"),
        "tape_hits": len(tape_census.get("hits") or []),
        "newcomer_handoffs": len(tape.get("newcomer_handoffs") or []),
    }


def health_section(readings: dict, now: datetime) -> dict:
    alerts = readings.get("alerts") or {}
    recent = alerts.get("recent") if isinstance(alerts.get("recent"), list) else []
    since = now - timedelta(hours=24)
    paged = []
    for item in recent:
        if not isinstance(item, dict):
            continue
        try:
            at = datetime.fromisoformat(str(item.get("at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        subject = str(item.get("subject") or "")
        if at >= since and ("] CRITICAL:" in subject or "] HIGH:" in subject):
            paged.append(subject.split("] ", 1)[-1][:60])
    blind = []
    for group in ("feed_other", "feed_watch"):
        blind.extend(sorted(((readings.get(group) or {}).get("blind_since") or {}).keys()))
    return {
        "alerts_healthy": alerts.get("healthy"),
        "undelivered": alerts.get("undelivered"),
        "paged_24h": paged,
        "blind_feeds": blind,
        "data_age": _age((readings.get("index") or {}).get("last_updated"), now),
        "risk": (readings.get("risk") or {}).get("level"),
        "risk_score": (readings.get("risk") or {}).get("score"),
        "scan_policy": (readings.get("scans") or {}).get("policy"),
    }


def build(readings: dict, config: dict | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    return {
        "day": now.strftime("%Y-%m-%d"),
        "computed_at": now.isoformat(),
        "target": target_section(readings, now),
        "hunt": hunt_section(readings, config),
        "execution": execution_section(readings),
        "health": health_section(readings, now),
    }


def digest_now(digest: dict) -> datetime:
    try:
        return datetime.fromisoformat(digest["computed_at"])
    except (KeyError, TypeError, ValueError):
        return datetime.now(UTC)


def render(digest: dict) -> tuple[str, str]:
    """(title, body) for a phone screen: short lines, the answer first."""
    t, h, e, s = digest["target"], digest["hunt"], digest["execution"], digest["health"]
    if t["unprecedented"]:
        silence = f"UNPRECEDENTED silence {t['silent_days']}d (longest ever {t['max_gap']}d)"
    elif t["unusual"]:
        silence = f"UNUSUAL silence {t['silent_days']}d (his p90 {t['p90']}d)"
    else:
        silence = f"silence {t['silent_days']}d, normal (p90 {t['p90']}d)"
    lines = [
        f"Target: last fill {t['last_fill']}; {silence}; value {_money(t['value'])}.",
    ]
    if h.get("his_wallets"):
        parts = []
        for row in h["his_wallets"]:
            last = row.get("last_fill_ms")
            state = (f"traded {_age(last, digest_now(digest))}" if last else "no fill seen")
            parts.append(f"{_short(row['address'])} {state}, {_money(row['value'])}")
        lines.append("His other wallets: " + "; ".join(parts) + ".")
    tiers = h["tiers"]
    lines.append(f"Hunt: {tiers['CONFIRMED']} confirmed / {tiers['PROBABLE']} probable / "
                 f"{tiers['POSSIBLE']} possible beyond his known wallets; {h['watched']} watched; "
                 f"{h['handoffs']} dormancy handoff(s).")
    if h["suspects"]:
        parts = []
        for row in h["suspects"]:
            p = row.get("p_now")
            pct = f"{100 * p:.2f}%" if isinstance(p, (int, float)) else "n/a"
            where = f"HL {_money(row['value'])}" if row.get("on_hl") else "not on HL"
            parts.append(f"{_short(row['address'])} {pct} ({where})")
        lines.append("Top suspects: " + "; ".join(parts) + ".")
    gate = "OPEN" if e["gate_open"] else "closed"
    ceiling = f"{100 * e['ceiling']:.1f}%" if isinstance(e["ceiling"], (int, float)) else "unmeasured"
    lines.append(f"Execution vote gate {gate}: false-match ceiling {ceiling} "
                 f"({e['attempted'] or 0} read, {e['comparable'] or 0} comparable); "
                 f"{e['discriminating']} rare match(es) among {e['read'] or 0} candidates read.")
    tape_ceiling = (f"{100 * e['tape_ceiling']:.1f}%" if isinstance(e["tape_ceiling"], (int, float))
                    else "unmeasured")
    lines.append(f"Tape: {e['sketch_wallets'] or 0} wallets sketched, ceiling {tape_ceiling}, "
                 f"{e['tape_hits']} program hit(s), {e['newcomer_handoffs']} newcomer handoff(s).")
    health = "healthy" if s["alerts_healthy"] else "FAILING"
    blind = f"; BLIND: {', '.join(s['blind_feeds'])}" if s["blind_feeds"] else "; feeds fresh"
    lines.append(f"System: alerts {health} ({s['undelivered'] or 0} undelivered){blind}; "
                 f"data {s['data_age']}; risk {s['risk']} {s['risk_score']}; "
                 f"behaviour policy {s['scan_policy']}.")
    if s["paged_24h"]:
        lines.append("Paged last 24h: " + " | ".join(s["paged_24h"][:4]) + ".")
    else:
        lines.append("Paged last 24h: nothing.")
    return f"Ezekiel daily {digest['day']}", "\n".join(lines)
