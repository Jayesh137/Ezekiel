# src/risk.py
"""Unified Migration Risk Score.

Turns every scattered signal into one 0-100 posture so the user has a single
number that answers "how likely is it the trader is migrating right now, and do we
have a lead?". High score = act now: check the top candidate.
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import (
    DATA_DIR,
    load_all_records,
    now_ms,
    read_cursor,
    save_latest,
    write_cursor,
)

# Points each signal contributes at full strength (sum = 100).
WEIGHTS = {
    "silence": 18,          # target stopped trading
    "drawdown": 12,         # account value collapsed (wiped -> fresh start)
    "l1_outbound": 8,       # USDC left on Arbitrum
    "hl_native_outbound": 10,   # funds moved inside HL to a non-self wallet
    "top_candidate": 22,    # a wallet trades like the target
    "correlation": 15,      # exit amount re-appeared as a fresh bridge deposit
    "linkage": 10,          # shared funder / address reuse with a candidate
    "xyz_abandoned": 5,     # stopped trading the signature HIP-3 markets
}


# Fund-movement signals only count as "migrating right now" for this long.
# Without a bound, one outbound transfer ever contributes points forever.
RECENT_SIGNAL_DAYS = 21


def _detected_ts(finding: dict) -> float:
    """Unix seconds for a fund-flow finding's detection time. Unparseable or
    missing timestamps are treated as ancient so they cannot latch a signal on."""
    raw = finding.get("detected_at")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _level(score: float) -> str:
    if score >= 75:
        return "CRITICAL"
    if score >= 50:
        return "ELEVATED"
    if score >= 25:
        return "GUARDED"
    return "LOW"


def compute_risk_score(signals: dict) -> dict:
    """Pure scoring. `signals` carries normalized inputs; returns score + factors."""
    factors = []

    def add(key, fraction, label):
        pts = round(WEIGHTS[key] * max(0.0, min(1.0, fraction)), 1)
        if pts > 0:
            factors.append({"signal": key, "points": pts, "label": label})
        return pts

    total = 0.0
    days_silent = signals.get("days_silent", 0) or 0
    total += add("silence", days_silent / 10.0, f"Target silent {days_silent:.1f}d")

    drawdown = signals.get("drawdown_pct", 0) or 0
    total += add("drawdown", drawdown / 0.5, f"Account down {drawdown*100:.0f}% from high-water")

    if signals.get("l1_outbound"):
        total += add("l1_outbound", 1.0, "Outbound USDC on Arbitrum L1")

    if signals.get("hl_native_outbound"):
        total += add("hl_native_outbound", 1.0, "Funds sent to a new wallet inside Hyperliquid")

    top = signals.get("top_candidate_score", 0) or 0
    if top >= 0.65:
        total += add("top_candidate", (top - 0.65) / 0.35, f"Behavioral candidate at {top*100:.0f}%")

    corr = signals.get("correlation_confidence", 0) or 0
    if corr > 0:
        total += add("correlation", corr, f"Deposit/withdrawal correlation {corr*100:.0f}%")

    if signals.get("linkage_hit"):
        total += add("linkage", 1.0, "Shared funder / address reuse with a candidate")

    if signals.get("xyz_abandoned"):
        total += add("xyz_abandoned", 1.0, "Stopped trading signature HIP-3 (xyz:) markets")

    score = round(min(100.0, total), 1)
    factors.sort(key=lambda f: f["points"], reverse=True)
    return {
        "score": score,
        "level": _level(score),
        "factors": factors,
        "top_candidate_wallet": signals.get("top_candidate_wallet"),
    }


def _xyz_abandoned(fills: list[dict], days: float = 10) -> bool:
    """True if the target used to trade xyz: markets but has not in `days` days."""
    xyz_times = [f.get("time", 0) for f in fills if str(f.get("coin", "")).startswith("xyz:")]
    if not xyz_times:
        return False
    last_xyz = max(xyz_times)
    age_days = (now_ms() - last_xyz) / 86_400_000
    return age_days >= days


def _gather_signals() -> dict:
    signals = {}

    # Silence
    last_fill = read_cursor("last_fill_time")
    signals["days_silent"] = (now_ms() - last_fill) / 86_400_000 if last_fill else 0

    # Drawdown vs the true high-water mark. This must NOT read
    # prev_account_value_cents: the collector resets that cursor when it fires a
    # drop alert, which drove drawdown_pct to 0.0 precisely when the account had
    # just collapsed. account_high_water_cents only ever ratchets upward.
    hw_cents = read_cursor("account_high_water_cents") or read_cursor("prev_account_value_cents")
    cur = 0.0
    acct_path = DATA_DIR / "account" / "latest.json"
    if acct_path.exists():
        try:
            data = json.load(open(acct_path))
            perp = data.get("perp", data) or {}
            cur = float(perp.get("marginSummary", {}).get("accountValue", 0) or 0)
        except Exception:
            pass
    if hw_cents and hw_cents > 0 and cur > 0:
        hw = hw_cents / 100.0
        signals["drawdown_pct"] = max(0.0, (hw - cur) / hw)
    else:
        signals["drawdown_pct"] = 0.0

    # Fund movement (L1 + HL-native). Both are time-bounded: fund_flows/latest.json
    # keeps the last 100 findings with no expiry, so an unbounded `any()` latched
    # this signal on permanently after a single historical transfer.
    ff_path = DATA_DIR / "fund_flows" / "latest.json"
    if ff_path.exists():
        try:
            findings = json.load(open(ff_path)).get("findings", [])
            cutoff = datetime.now(UTC).timestamp() - RECENT_SIGNAL_DAYS * 86400
            signals["l1_outbound"] = any(
                f.get("amount_usdc_raw", 0) and _detected_ts(f) >= cutoff
                for f in findings
            )
        except Exception:
            signals["l1_outbound"] = False

    hl_path = DATA_DIR / "hl_transfers" / "latest.json"
    if hl_path.exists():
        try:
            cps = json.load(open(hl_path)).get("counterparties", [])
            recent_cut = now_ms() - 21 * 86_400_000
            signals["hl_native_outbound"] = any(
                (not c.get("known_self")) and c.get("total_out_usd", 0) >= 50_000
                and c.get("last_seen_ms", 0) >= recent_cut
                for c in cps
            )
        except Exception:
            signals["hl_native_outbound"] = False

    # Top behavioral candidate
    cand_path = DATA_DIR / "candidates" / "latest.json"
    if cand_path.exists():
        try:
            cands = json.load(open(cand_path)).get("candidates", [])
            cands.sort(key=lambda c: c.get("best_score", 0), reverse=True)
            if cands:
                signals["top_candidate_score"] = float(cands[0].get("best_score", 0))
                signals["top_candidate_wallet"] = cands[0].get("wallet")
                signals["linkage_hit"] = any(
                    c.get("latest_evidence", {}).get("linkage", {}).get("shared_funder")
                    or c.get("latest_evidence", {}).get("linkage", {}).get("shared_deposit_addresses")
                    for c in cands
                )
        except Exception:
            pass

    # Deposit/withdrawal correlation
    corr_path = DATA_DIR / "correlations" / "latest.json"
    if corr_path.exists():
        try:
            matches = json.load(open(corr_path)).get("matches", [])
            if matches:
                signals["correlation_confidence"] = float(matches[0].get("confidence", 0))
        except Exception:
            pass

    # xyz: signature-market abandonment
    signals["xyz_abandoned"] = _xyz_abandoned(load_all_records(str(DATA_DIR / "fills")))
    return signals


def run_risk() -> dict:
    """Compute and persist the risk score; alert when the level rises."""
    from src.alerts import alert_risk_level

    signals = _gather_signals()
    result = compute_risk_score(signals)
    result["computed_at"] = datetime.now(UTC).isoformat()
    result["signals"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in signals.items()}
    save_latest(str(DATA_DIR / "risk"), result)

    print(f"[risk] Migration risk: {result['score']}/100 ({result['level']})")
    for f in result["factors"]:
        print(f"[risk]   +{f['points']} {f['label']}")

    # Alert only when the level increases (e.g. GUARDED -> ELEVATED), with cooldown.
    order = {"LOW": 0, "GUARDED": 1, "ELEVATED": 2, "CRITICAL": 3}
    prev = read_cursor("risk_level_ord")
    cur_ord = order[result["level"]]
    if cur_ord > (prev or 0) and cur_ord >= order["ELEVATED"]:
        if alert_risk_level(result["score"], result["level"], result["factors"],
                            result.get("top_candidate_wallet")):
            write_cursor("risk_level_ord", cur_ord)
    else:
        write_cursor("risk_level_ord", cur_ord)

    return result


def main():
    run_risk()


if __name__ == "__main__":
    main()
