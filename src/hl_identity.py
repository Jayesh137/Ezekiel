# src/hl_identity.py
"""What Hyperliquid itself will say about any address, asked properly.

The mission's deliverable is a Hyperliquid address, and every EVM address is
one. Before this module the system asked Hyperliquid about an address in five
different partial ways — perp state here, fills there, `extraAgents` for the
target alone — and never the questions that resolve IDENTITY outright:

  userRole       user / vault / subAccount (-> its master) / agent (-> its
                 owner) / missing. An agent address is a wallet someone
                 explicitly authorised; the owner is the person.
  webData2       the account's CURRENT frontend agent (`agentAddress`), which
                 `extraAgents` never lists — measured 2026-09-10 the target's
                 baseline said "no agents" while webData2 showed one approved
                 four days earlier.
  userFees       `stakingLink`, an explicit link between a staking wallet and
                 a trading wallet: an act of control, like an agent.
  delegations    which validators an account stakes with, and how much.
  portfolio      the all-time value series, whose first non-zero point is the
                 account's real birth. Fills cannot give this: the API keeps
                 only the last ~10,000, so a busy wallet's "first fill" is
                 last week.

`probe` is I/O with an injectable fetch; everything else is pure so the
readings can be tested and re-interpreted offline. A failed read is recorded
as a failed read — `read_ok: False` — never as "nothing here".
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, save_latest

IDENTITY_DIR = DATA_DIR / "identity"


def parse_role(payload) -> dict:
    """{"role": ..., "master": ..., "owner": ...} from a userRole answer."""
    out = {"role": None, "master": None, "owner": None}
    if not isinstance(payload, dict):
        return out
    role = payload.get("role")
    out["role"] = role if isinstance(role, str) else None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if role == "subAccount":
        out["master"] = (data.get("master") or "").lower() or None
    elif role == "agent":
        out["owner"] = (data.get("user") or "").lower() or None
    return out


def parse_web_data(payload) -> dict:
    """The frontend agent, vault leadership and account value from webData2."""
    out = {"agent_address": None, "agent_valid_until": None, "leading_vaults": [],
           "is_vault": None, "account_value": None}
    if not isinstance(payload, dict):
        return out
    agent = payload.get("agentAddress")
    # Lowercase first: a checksummed address starts "0X" under .upper().
    agent = agent.strip().lower() if isinstance(agent, str) else None
    if agent and agent.startswith("0x") and len(agent) == 42:
        out["agent_address"] = agent
    out["agent_valid_until"] = payload.get("agentValidUntil")
    lv = payload.get("leadingVaults")
    out["leading_vaults"] = sorted(
        (v.get("address") if isinstance(v, dict) else v).lower()
        for v in (lv if isinstance(lv, list) else [])
        if isinstance(v.get("address") if isinstance(v, dict) else v, str))
    out["is_vault"] = payload.get("isVault")
    try:
        out["account_value"] = float(
            ((payload.get("clearinghouseState") or {}).get("marginSummary") or {})
            .get("accountValue"))
    except (TypeError, ValueError):
        out["account_value"] = None
    return out


def parse_staking_link(payload) -> dict | None:
    """{"staking_user", "trading_user"} from userFees, or None when unlinked."""
    if not isinstance(payload, dict):
        return None
    link = payload.get("stakingLink")
    if not isinstance(link, dict):
        return None
    s = (link.get("stakingUser") or "").lower()
    t = (link.get("tradingUser") or "").lower()
    if not (s and t):
        return None
    return {"staking_user": s, "trading_user": t}


def parse_delegations(payload) -> dict:
    """validator -> HYPE delegated."""
    out: dict[str, float] = {}
    for d in payload if isinstance(payload, list) else []:
        if not isinstance(d, dict):
            continue
        v = (d.get("validator") or "").lower()
        try:
            amt = float(d.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if v and amt > 0:
            out[v] = out.get(v, 0.0) + amt
    return out


def parse_birth(payload) -> int | None:
    """Birth in ms: the first non-zero point of the all-time value series.

    None when there is no such point — the account has never held value —
    and never 0, which would read as 1970.
    """
    if not isinstance(payload, list):
        return None
    for item in payload:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            continue
        name, series = item
        if name != "allTime" or not isinstance(series, dict):
            continue
        for point in series.get("accountValueHistory") or []:
            try:
                ts, value = int(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
            if value > 0:
                return ts
    return None


def probe(address: str, fetch, *, sleep=None) -> dict:
    """Ask every identity-resolving endpoint about one address.

    `fetch(body)` is hl_post or a fake. Each endpoint is tried on its own so
    one failure does not blank the others, and every failure is named.
    """
    a = (address or "").lower()
    out = {"address": a, "read_ok": True, "errors": [], "role": None,
           "master": None, "owner": None, "agent_address": None,
           "agent_valid_until": None, "leading_vaults": [], "is_vault": None,
           "account_value": None, "staking_link": None, "delegations": {},
           "birth_ms": None, "checked_at": datetime.now(UTC).isoformat()}

    def ask(kind, body):
        try:
            got = fetch(body)
        except Exception as exc:                      # noqa: BLE001 - transport
            out["errors"].append(f"{kind}: {type(exc).__name__}: {exc}")
            out["read_ok"] = False
            return None
        if sleep:
            sleep(0.15)
        return got

    role = ask("userRole", {"type": "userRole", "user": a})
    if role is not None:
        out.update(parse_role(role))
    web = ask("webData2", {"type": "webData2", "user": a})
    if web is not None:
        out.update(parse_web_data(web))
    fees = ask("userFees", {"type": "userFees", "user": a})
    if fees is not None:
        out["staking_link"] = parse_staking_link(fees)
    dele = ask("delegations", {"type": "delegations", "user": a})
    if dele is not None:
        out["delegations"] = parse_delegations(dele)
    pf = ask("portfolio", {"type": "portfolio", "user": a})
    if pf is not None:
        out["birth_ms"] = parse_birth(pf)
    return out


def present(identity: dict) -> bool | None:
    """Does Hyperliquid know this address at all? None if the read failed."""
    if not identity or not identity.get("read_ok", False):
        return None
    if identity.get("role") not in (None, "missing"):
        return True
    if identity.get("role") == "missing":
        return False
    return None


def explicit_links(identities: dict, cluster: set) -> list[dict]:
    """Deliberate acts of control tying an address to the cluster. Pure.

    Three kinds, each strong enough to stand alone:
      * an address whose AGENT OWNER is a cluster wallet (`userRole` agent)
      * a SUB-ACCOUNT whose master is a cluster wallet
      * a STAKING LINK whose other side is a cluster wallet
    And the reverse for each: a cluster wallet that is somebody's agent or
    sub-account names that somebody.
    """
    cluster = {(c or "").lower() for c in cluster}
    links = []
    for addr, ident in (identities or {}).items():
        a = (addr or "").lower()
        if not a:
            continue
        owner = ident.get("owner")
        master = ident.get("master")
        link = ident.get("staking_link") or {}
        if owner and (owner in cluster or a in cluster) and owner != a:
            links.append({"kind": "agent", "address": a, "linked_to": owner,
                          "why": "authorised as an agent by this account"})
        if master and (master in cluster or a in cluster) and master != a:
            links.append({"kind": "subaccount", "address": a, "linked_to": master,
                          "why": "is a sub-account of this master account"})
        if link:
            other = link["trading_user"] if link["staking_user"] == a else link["staking_user"]
            if other and other != a and (other in cluster or a in cluster):
                links.append({"kind": "staking_link", "address": a, "linked_to": other,
                              "why": "staking link declared between the two accounts"})
    return links


def save(report: dict) -> None:
    save_latest(str(IDENTITY_DIR), report)
