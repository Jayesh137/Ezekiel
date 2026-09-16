# src/hl_surface.py
"""What Hyperliquid itself declares about an account: sub-accounts, referral, vaults.

These three endpoints were asked about the TARGET alone (the collector) and the
handful of wallets under close watch. For him they are empty — `subAccounts`
answers `null`, he has no referral code and no referrer, and he deposits into
no vault — so the scanner's overlap checks built on them returned early for
every wallet, every run, and nothing asked the candidates at all.

Measured live 2026-09-16 over the 25 strongest roster wallets: **31 sub-account
addresses under 5 of them, none in the roster, the graph or the identity
cache**, one holding $1.26M; and `0xfe7ce058…` referred by `0xa312114b…`, both
on the roster. A sub-account is an address the owner can copy on Hyperliquid
directly — the deliverable's exact shape.

What each finding is worth:

  * A SUB-ACCOUNT exists only because its master created it. Between a cluster
    wallet and anyone else that is an act of control, the same fact `userRole`
    reports from the other side, and it CONFIRMs alone.
  * A REFERRAL is chosen by the account that uses the code, so it is an
    association, not control. It counts only when the code is QUIET — an
    influencer's code links everyone who ever typed it (rule 9) — and an
    unmeasured code never counts.
  * A VAULT deposit is evidence only, after the shared vaults (HLP) are removed.

Pure: parsers, link derivation and transition diffing. The script does I/O.
"""

from datetime import datetime

from src import referral
from src.utils import DATA_DIR, save_latest

HL_SURFACE_DIR = DATA_DIR / "hl_surface"

# A code used by more accounts than this is somebody's public code.
QUIET_REFERRALS = 10
# An empty sub-account has nothing to copy; it joins the roster once funded.
MIN_SUB_VALUE_USD = 1000.0
# How many accounts use a code rarely falls, so a week-old count still answers.
REFERRER_RECHECK_DAYS = 7


def _addr(value) -> str | None:
    if isinstance(value, str) and value.lower().startswith("0x"):
        return value.lower()
    return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_subaccounts(payload, master: str) -> list[dict] | None:
    """Rows for a `subAccounts` answer. `null` is the API saying "none"; anything
    that is neither null nor a list is a failed read and returns None."""
    if payload is None:
        return []
    if not isinstance(payload, list):
        return None
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        address = _addr(item.get("subAccountUser"))
        if not address:
            continue
        summary = ((item.get("clearinghouseState") or {}).get("marginSummary") or {})
        rows.append({"address": address,
                     "master": _addr(item.get("master")) or master.lower(),
                     "name": item.get("name"),
                     "account_value": _float(summary.get("accountValue"))})
    return rows


def parse_vaults(payload) -> list[str] | None:
    if not isinstance(payload, list):
        return None
    return [a for a in (_addr((v or {}).get("vaultAddress")) for v in payload
                        if isinstance(v, dict)) if a]


def referral_reading(payload) -> dict | None:
    """A successful `referral` read is always a populated document, so an empty
    or non-dict payload is a failure — never "referred by nobody"."""
    if not isinstance(payload, dict) or not payload:
        return None
    return {"referred_by": referral.referred_by(payload),
            "code": referral.code(payload),
            "referred": [r["address"] for r in referral.referred(payload)]}


def _code_size(referrer: str, readings: dict, sizes: dict) -> int | None:
    """How many accounts use `referrer`'s code, or None if we never measured it.
    A referrer we read this run answers from its own reading."""
    own = (readings.get(referrer) or {}).get("referral")
    if isinstance(own, dict) and own.get("code"):
        return len(own.get("referred") or [])
    size = (sizes.get(referrer) or {}).get("accounts")
    return size if isinstance(size, int) else None


def referrers_to_measure(readings: dict, sizes: dict, now: datetime, limit: int) -> list[str]:
    """Referrers whose code size is unknown or older than a week, in reading order."""
    out = []
    for reading in readings.values():
        ref = ((reading or {}).get("referral") or {}).get("referred_by")
        if not ref or ref in out:
            continue
        # A referrer read this run answers from its own reading — unless that
        # read failed, in which case it still needs measuring.
        if isinstance((readings.get(ref) or {}).get("referral"), dict):
            continue
        measured = (sizes.get(ref) or {}).get("measured_at")
        try:
            fresh = (now - datetime.fromisoformat(measured)).days < REFERRER_RECHECK_DAYS
        except (TypeError, ValueError):
            fresh = False
        if not fresh:
            out.append(ref)
        if len(out) >= limit:
            break
    return out


def build_report(readings: dict, referrer_sizes: dict, cluster: set,
                 shared_vaults: set) -> dict:
    """Derive sub-accounts, referrals, vault deposits and links. Pure."""
    cluster = {c.lower() for c in cluster if c}
    subaccounts: dict[str, dict] = {}
    referrals: dict[str, dict] = {}
    vault_deposits: dict[str, list] = {}
    links: list[dict] = []
    seen: set = set()

    def link(kind, address, linked_to, why, **extra):
        key = (kind, address, linked_to)
        if address != linked_to and key not in seen:
            seen.add(key)
            links.append({"kind": kind, "address": address, "linked_to": linked_to,
                          "why": why, **extra})

    def referral_link(referred, referrer, size):
        if referred in cluster or referrer in cluster:
            if size is not None and size <= QUIET_REFERRALS:
                link("referral", referred, referrer,
                     "used this account's referral code", code_accounts=size)
        else:
            link("referral_pair", referred, referrer,
                 "used this account's referral code", code_accounts=size)

    for wallet, reading in readings.items():
        reading = reading or {}
        for sub in reading.get("subaccounts") or []:
            subaccounts[sub["address"]] = {k: sub[k] for k in
                                           ("master", "name", "account_value")}
            if sub["master"] in cluster or sub["address"] in cluster:
                link("subaccount", sub["address"], sub["master"],
                     "is a sub-account of this master account")

        vaults = [v for v in (reading.get("vaults") or []) if v not in shared_vaults]
        if vaults:
            vault_deposits[wallet] = vaults

        ref = reading.get("referral")
        if not isinstance(ref, dict):
            continue
        referrer = ref.get("referred_by")
        if referrer:
            size = _code_size(referrer, readings, referrer_sizes)
            referrals[wallet] = {"referrer": referrer, "referrer_accounts": size}
            referral_link(wallet, referrer, size)
        if ref.get("code") and wallet in cluster:
            size = len(ref.get("referred") or [])
            for referred in ref.get("referred") or []:
                referral_link(referred, wallet, size)

    return {"subaccounts": subaccounts, "referrals": referrals,
            "vault_deposits": vault_deposits, "links": links}


def link_key(link: dict) -> tuple:
    return (link.get("kind"), link.get("address"), link.get("linked_to"))


def new_links(previous: dict | None, current: dict) -> list[dict]:
    """Links not in the previous report. An absent previous report is EMPTY:
    the cluster had no such link when this was built, so there is nothing to
    seed, and an arriving link must never be swallowed as a first reading."""
    before = {link_key(x) for x in ((previous or {}).get("links") or [])}
    return [x for x in (current.get("links") or []) if link_key(x) not in before]


def save(report: dict) -> None:
    save_latest(str(HL_SURFACE_DIR), report)
