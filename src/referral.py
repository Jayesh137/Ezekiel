# src/referral.py
"""A referral code is a human-chosen label, and the accounts it refers are
addresses the referrer knows.

The target has no code (`referrerState.stage` is `needToCreateCode`, measured
2026-09-12) and was referred by nobody. That is a baseline worth guarding,
not a dead end: a trader opening a second account has every reason to refer
it from the first — the rebate is free money — and the moment he creates a
code, `referrerState.data.referralStates` names every account that used it.
Those are addresses he chose to link to himself, which is the strongest kind
of lead this project can be handed.

Pure: `changes` diffs two stored readings; `referred` lists what a reading
names. The collector does the I/O.
"""


def stage(reading: dict | None) -> str | None:
    state = (reading or {}).get("referrerState")
    if not isinstance(state, dict):
        return None
    return state.get("stage")


def code(reading: dict | None) -> str | None:
    state = (reading or {}).get("referrerState")
    if not isinstance(state, dict):
        return None
    data = state.get("data")
    if isinstance(data, dict):
        return data.get("code")
    return None


def referred(reading: dict | None) -> list[dict]:
    """(address, cumVlm) for every account this reading says used the code."""
    state = (reading or {}).get("referrerState")
    if not isinstance(state, dict):
        return []
    data = state.get("data")
    rows = data.get("referralStates") if isinstance(data, dict) else None
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        addr = (r.get("user") or r.get("address") or "")
        # `0x` survives .lower(); an upper-cased address arrives as `0X`.
        if isinstance(addr, str) and addr.lower().startswith("0x"):
            out.append({"address": addr.lower(), "cum_vlm": r.get("cumVlm"),
                        "cum_rewarded_fees": r.get("cumRewardedFeesSinceReferred")})
    return out


def referred_by(reading: dict | None) -> str | None:
    rb = (reading or {}).get("referredBy")
    if isinstance(rb, dict):
        ref = rb.get("referrer")
        return ref.lower() if isinstance(ref, str) else None
    return None


def changes(previous: dict | None, current: dict | None) -> list[dict]:
    """What moved between two readings. Pure.

    A missing previous reading is a first reading, not a change: nothing has
    happened yet that anybody needs to be told about. A current reading that
    is not a dict is a failed read and diffs as nothing — a failure must never
    look like a code being deleted.
    """
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return []
    out = []
    if stage(previous) != stage(current) or code(previous) != code(current):
        out.append({"kind": "referral_code", "before": {"stage": stage(previous), "code": code(previous)},
                    "after": {"stage": stage(current), "code": code(current)}})
    before = {r["address"] for r in referred(previous)}
    out.extend({"kind": "referred_account", "address": r["address"], "cum_vlm": r.get("cum_vlm")}
               for r in referred(current) if r["address"] not in before)
    if referred_by(previous) != referred_by(current) and referred_by(current):
        out.append({"kind": "referred_by", "referrer": referred_by(current)})
    return out


__all__ = ["stage", "code", "referred", "referred_by", "changes"]
