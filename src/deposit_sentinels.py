# src/deposit_sentinels.py
"""His private exchange deposit addresses, and anyone else who pays into them.

A CEX deposit address belongs to one exchange account. `linkage.py` has used
that since 2026-09-10 — "address reuse is the strongest single signal" — but
only from one side: it compares the OUTBOUND destinations of wallets we have
swept. A wallet we never swept is invisible to it however many times it paid
his deposit address, even though the deposit address's own sweep holds every
payment it ever received.

Measured 2026-09-16 on the one that is live, `0x8570c2ae…` (forwards 100% of
$230.9M to Binance; the treasury paid it on 2026-08-15): every sender it has
ever had is the target, the treasury, `0xf078969e…` — and
`0xda0932d2a880bafa82bc2ac41ab0caafc5544f52`, which paid it $249,993.84 on
2024-07-31 and appeared nowhere in the roster, the graph or any report. Its
whole life was that day: exchange-funded gas and USDC, a $6 Hyperliquid deposit
that opened an account the same day, and everything onward to his deposit
address. The transfer graph grades the deposit address a conduit SERVICE, and a
service's senders are linked to nobody.

So this module reads the relationship from the deposit address's side:

- A **sentinel** is an address the graph infers is an exchange deposit address
  (a conduit, or an inferred deposit address), that his config cluster paid at
  least `MIN_CLUSTER_USD`, and that the whole chain says is a quiet EOA. Rule 9
  decides the last part: a shared deposit address (memo-style, or an exchange's
  own sweeper) is busy, and its senders share nothing. Unmeasured is pending,
  never a sentinel. An operator-configured `sentinel_addresses` entry is ground
  truth and needs no inference.
- Membership is **sticky**, kept in this detector's own file. A deposit address
  is a permanent fact; the graph that inferred it is volatile (node budgets,
  decaying edges), and a finding reachable only through another detector's
  output is one vector wearing two names — the lesson
  `roster.linkage_from_first_funders` was written for.
- A **sharer** is a non-cluster sender that paid a sentinel at least
  `MIN_SENDER_USD` of measured value. Dust and zero-value transfers are the
  shape of address poisoning (`0xf0775c88…719e` imitating `0xf078969e…` is in
  this very address's history) and never count. A sender measured busy — an
  exchange hot wallet or a bridge delivering to the address — is him moving
  between his own exchange accounts, recorded but not a new wallet.

All pure: the I/O lives in `scripts/check_deposit_sentinels.py`.
"""

from pathlib import Path

# His cluster must have put real money in before an address is called his.
MIN_CLUSTER_USD = 1_000.0
# Below this a payment is dust, and dust into a known address is poisoning.
MIN_SENDER_USD = 100.0

# The graph's words for "an exchange deposit address".
FORWARDING_REASONS = ("conduit:", "inferred exchange deposit address")

CLASS_QUIET = "quiet"
CLASS_BUSY = "busy"
CLASS_CONTRACT = "contract"
CLASS_UNMEASURED = "unmeasured"


def _low(a) -> str:
    return (a or "").strip().lower()


def classify(readings) -> str:
    """One address's class from its per-chain activity readings.

    Busy on ANY chain is busy — an exchange sweeper is infrastructure wherever
    it was counted. Quiet needs at least one reading and no busy one. A contract
    is never a person. No reading at all is unmeasured, which is never quiet.
    """
    from src.chain.activity import is_busy

    readings = [r for r in (readings or []) if isinstance(r, dict)]
    if not readings:
        return CLASS_UNMEASURED
    if any(r.get("is_contract") for r in readings):
        return CLASS_CONTRACT
    if any(is_busy(r) for r in readings):
        return CLASS_BUSY
    return CLASS_QUIET


def forwarding_candidates(nodes, inferred: dict | None) -> dict[str, str]:
    """Addresses something has already inferred are exchange deposit addresses."""
    out: dict[str, str] = {}
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        reason = str(((node.get("evidence") or {}).get("service_reason")) or "")
        if reason.startswith(FORWARDING_REASONS):
            out[_low(node.get("wallet"))] = reason
    for addr, info in ((inferred or {}).get("addresses") or {}).items():
        a = _low(addr)
        if a and a not in out:
            entity = (info or {}).get("entity") if isinstance(info, dict) else None
            out[a] = f"inferred exchange deposit address: {entity or 'forwards to a hot wallet'}"
    out.pop("", None)
    return out


def cluster_payments(records, cluster: set) -> dict[str, dict]:
    """Valued payments FROM the config cluster, per destination. Pure."""
    cluster = {_low(c) for c in cluster}
    out: dict[str, dict] = {}
    for rec in records or []:
        src, dst = _low(rec.get("src")), _low(rec.get("dst"))
        if src not in cluster or not dst or dst in cluster:
            continue
        usd = rec.get("amount_usd")
        if usd is None:
            continue
        try:
            usd = float(usd)
        except (TypeError, ValueError):
            continue
        entry = out.setdefault(dst, {"usd": 0.0, "chains": set(), "last_ts": 0})
        entry["usd"] += usd
        if rec.get("chain"):
            entry["chains"].add(rec["chain"])
        try:
            entry["last_ts"] = max(entry["last_ts"], int(rec.get("ts") or 0))
        except (TypeError, ValueError):
            pass
    return out


def select_sentinels(candidates: dict, paid: dict, classes: dict,
                     previous: dict | None, configured) -> tuple[dict, dict, dict]:
    """(sentinels, pending, excluded). Pure.

    `classes` maps address -> CLASS_*. `previous` is the stored sentinel map, so
    membership survives the graph forgetting an address; a previous sentinel is
    dropped only when it is now MEASURED busy or a contract, never because an
    inference lapsed.
    """
    sentinels: dict[str, dict] = {}
    pending: dict[str, str] = {}
    excluded: dict[str, str] = {}

    for addr in sorted({_low(a) for a in configured or [] if a}):
        sentinels[addr] = {"reason": "configured by the operator",
                           "class": classes.get(addr, CLASS_UNMEASURED),
                           "cluster_paid_usd": round((paid.get(addr) or {}).get("usd", 0.0), 2)}

    for addr, reason in sorted((candidates or {}).items()):
        if addr in sentinels:
            continue
        payment = paid.get(addr) or {}
        if payment.get("usd", 0.0) < MIN_CLUSTER_USD:
            continue
        cls = classes.get(addr, CLASS_UNMEASURED)
        if cls == CLASS_QUIET:
            sentinels[addr] = {"reason": reason, "class": cls,
                               "cluster_paid_usd": round(payment["usd"], 2),
                               "chains": sorted(payment.get("chains") or [])}
        elif cls == CLASS_UNMEASURED:
            pending[addr] = reason
        else:
            excluded[addr] = f"{cls}: {reason}"

    for addr, prior in ((previous or {}).get("sentinels") or {}).items():
        a = _low(addr)
        if not a or a in sentinels:
            continue
        cls = classes.get(a, CLASS_UNMEASURED)
        if cls in (CLASS_BUSY, CLASS_CONTRACT):
            excluded[a] = f"{cls}: was a sentinel, now measured {cls}"
            continue
        sentinels[a] = {**(prior or {}), "class": cls,
                        "reason": (prior or {}).get("reason") or "kept from a previous run"}
        pending.pop(a, None)

    for addr, entry in sentinels.items():
        prior = ((previous or {}).get("sentinels") or {}).get(addr) or {}
        entry["since"] = prior.get("since") or entry.get("since")
    return sentinels, pending, excluded


def senders(records, sentinel: str, cluster: set) -> tuple[dict, set]:
    """(valued senders, unvalued-only senders) into one sentinel. Pure.

    Only measured value counts toward `MIN_SENDER_USD`. A sender whose every
    payment is unvalued is kept apart: it may be real, but it cannot be told
    from a poisoner by amount, so it is recorded and never alerts.
    """
    sentinel = _low(sentinel)
    cluster = {_low(c) for c in cluster}
    valued: dict[str, dict] = {}
    unvalued: set = set()
    for rec in records or []:
        if _low(rec.get("dst")) != sentinel or rec.get("spam"):
            continue
        src = _low(rec.get("src"))
        if not src or src == sentinel or src in cluster:
            continue
        usd = rec.get("amount_usd")
        try:
            usd = None if usd is None else float(usd)
        except (TypeError, ValueError):
            usd = None
        if usd is None:
            unvalued.add(src)
            continue
        entry = valued.setdefault(src, {"usd": 0.0, "count": 0, "first_ts": None,
                                        "last_ts": None, "chains": set(), "assets": set(),
                                        "tx_hash": None})
        entry["usd"] += usd
        entry["count"] += 1
        try:
            ts = int(rec.get("ts") or 0) or None
        except (TypeError, ValueError):
            ts = None
        if ts:
            if entry["first_ts"] is None or ts < entry["first_ts"]:
                entry["first_ts"], entry["tx_hash"] = ts, rec.get("tx_hash")
            entry["last_ts"] = max(entry["last_ts"] or 0, ts)
        if rec.get("chain"):
            entry["chains"].add(rec["chain"])
        if rec.get("asset"):
            entry["assets"].add(rec["asset"])
    real = {a: e for a, e in valued.items() if e["usd"] >= MIN_SENDER_USD}
    unvalued -= set(valued)
    return real, unvalued


def sharers(sentinel_senders: dict, classes: dict) -> list[dict]:
    """Every valued outside sender, with its class. Pure and serialisable."""
    out = []
    for sentinel, found in sorted((sentinel_senders or {}).items()):
        for addr, e in sorted(found.items()):
            out.append({"sentinel": sentinel, "address": addr,
                        "class": classes.get(addr, CLASS_UNMEASURED),
                        "usd": round(e["usd"], 2), "count": e["count"],
                        "first_ts": e["first_ts"], "last_ts": e["last_ts"],
                        "chains": sorted(e["chains"]), "assets": sorted(e["assets"]),
                        "tx_hash": e["tx_hash"]})
    return out


def sharer_key(row: dict) -> str:
    return f"{_low(row.get('sentinel'))}:{_low(row.get('address'))}"


def news(previous: dict | None, current_sharers: list[dict]) -> list[dict]:
    """Sharers worth an alert this run. Pure.

    A sentinel's first reading is its baseline: its existing senders are the
    history that made it worth watching, not news it just made — the same rule
    `watchlist.changes` applies, and the reason `0xda0932d2…` (2024-07-31) is
    written up rather than announced. After that, a sender not seen before is
    news if it is a quiet EOA (CRITICAL) or could not be measured (HIGH).
    A busy sender or a contract is recorded and never alerts.
    """
    previous = previous or {}
    known = {s: {_low(a) for a in (addrs or [])}
             for s, addrs in (previous.get("known_senders") or {}).items()}
    out = []
    for row in current_sharers:
        seen = known.get(row["sentinel"])
        if seen is None or row["address"] in seen:
            continue
        if row["class"] in (CLASS_QUIET, CLASS_UNMEASURED):
            out.append(row)
    return out


def known_senders(previous: dict | None, sentinel_senders: dict,
                  pending_alerts: list[dict]) -> dict[str, list]:
    """Valued senders recorded as seen, excluding any whose alert is undelivered.

    An undelivered sharer must stay out of the seen set, or the next run would
    find nothing new and the alert would never be retried. Unvalued senders are
    deliberately NOT recorded as seen: an unpriced transfer long ago must not
    silence the same address paying real money today.
    """
    held = {sharer_key(r) for r in pending_alerts or []}
    out: dict[str, list] = {}
    for sentinel in sentinel_senders:
        addrs = set(sentinel_senders.get(sentinel) or {})
        prior = set(((previous or {}).get("known_senders") or {}).get(sentinel) or [])
        keep = {a for a in addrs | prior if f"{sentinel}:{a}" not in held}
        out[sentinel] = sorted(keep)
    return out


def severity(row: dict) -> str:
    return "CRITICAL" if row.get("class") == CLASS_QUIET else "HIGH"


def roster_sharers(report: dict | None) -> dict[str, dict]:
    """Wallets that earn a linkage vote: quiet EOAs that paid a sentinel."""
    out: dict[str, dict] = {}
    for row in (report or {}).get("sharers") or []:
        if row.get("class") != CLASS_QUIET:
            continue
        a = _low(row.get("address"))
        prev = out.get(a)
        if prev is None or float(row.get("usd") or 0) > float(prev.get("usd") or 0):
            out[a] = row
    return out


def sentinel_dir() -> Path:
    """Resolved at call time: a path captured at import escapes the test sandbox."""
    from src import utils
    return utils.DATA_DIR / "deposit_sentinels"


def save(report: dict, directory: Path | None = None) -> None:
    from src.utils import save_latest
    save_latest(str(directory or sentinel_dir()), report)
