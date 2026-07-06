# src/linkage.py
"""L1 wallet-clustering heuristics used to confirm a candidate is the same person.

Two research-backed signals, applied to promising behavioral candidates:

1. Shared funding source (first-funder heuristic). The first funds a fresh wallet
   receives usually come from a CEX withdrawal or a wallet the owner controls. If a
   candidate's first funder is the target itself, or the SAME funder the target was
   first funded by, that is a strong ownership link.

2. Address reuse (highest-confidence heuristic — cryptographic certainty). A CEX
   deposit address is unique to one account. If the candidate sends USDC to the
   SAME address the target sends to, they almost certainly share a CEX account.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import load_config, etherscan_get, load_all_records, DATA_DIR


def compute_linkage(candidate: str, candidate_first_funder: str | None,
                    candidate_out_addrs: set, target: str,
                    target_first_funder: str | None, target_out_addrs: set,
                    excluded: set) -> dict:
    """Pure linkage evaluation. Returns evidence, a score bonus, and reasons.

    Separated from Etherscan I/O so the scoring logic is unit-testable.
    """
    candidate = candidate.lower()
    target = target.lower()
    cff = (candidate_first_funder or "").lower()
    tff = (target_first_funder or "").lower()
    excluded = {a.lower() for a in excluded}

    reasons = []
    bonus = 0.0
    shared_funder = False
    shared_deposit = sorted(
        (candidate_out_addrs & target_out_addrs) - excluded - {target, candidate}
    )

    # Shared funding source
    if cff and cff not in excluded:
        if cff == target:
            shared_funder = True
            bonus += 0.15
            reasons.append("First funded directly by the target wallet")
        elif tff and cff == tff and tff not in excluded:
            shared_funder = True
            bonus += 0.12
            reasons.append("Shares the target's original funding source (same CEX/funder)")

    # Address reuse — send to the same (CEX deposit) address
    if shared_deposit:
        bonus += 0.18
        reasons.append(
            f"Sends USDC to the same address as target (address reuse): "
            f"{', '.join(a[:10] + '...' for a in shared_deposit[:2])}"
        )

    return {
        "shared_funder": shared_funder,
        "candidate_first_funder": cff or None,
        "shared_deposit_addresses": shared_deposit,
        "linkage_bonus": round(min(bonus, 0.30), 4),
        "reasons": reasons,
    }


def get_first_funder(wallet: str) -> str | None:
    """Earliest external address to send this wallet ETH or USDC on Arbitrum."""
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return None
    wl = wallet.lower()

    # Earliest normal (ETH) inbound tx — funds gas.
    normal = etherscan_get({
        "module": "account", "action": "txlist", "address": wallet,
        "startblock": 0, "endblock": 99999999, "page": 1, "offset": 20, "sort": "asc",
    })
    for t in normal.get("result", []) if normal.get("status") == "1" else []:
        frm = (t.get("from", "") or "").lower()
        if frm and frm != wl and int(t.get("value", 0) or 0) > 0:
            return frm

    # Fall back to earliest USDC inbound.
    config = load_config()
    tok = etherscan_get({
        "module": "account", "action": "tokentx", "address": wallet,
        "contractaddress": config["usdc_contract_arbitrum"],
        "page": 1, "offset": 20, "sort": "asc",
    })
    for t in tok.get("result", []) if tok.get("status") == "1" else []:
        to = (t.get("to", "") or "").lower()
        frm = (t.get("from", "") or "").lower()
        if to == wl and frm and frm != wl:
            return frm
    return None


def get_outbound_usdc_addresses(wallet: str, limit: int = 300) -> set:
    """Addresses this wallet has sent USDC to on Arbitrum (candidate CEX deposit
    addresses). Excludes the HL bridge itself."""
    if not os.environ.get("ETHERSCAN_API_KEY"):
        return set()
    config = load_config()
    bridge = config["hl_bridge_contract"].lower()
    wl = wallet.lower()
    res = etherscan_get({
        "module": "account", "action": "tokentx", "address": wallet,
        "contractaddress": config["usdc_contract_arbitrum"],
        "page": 1, "offset": limit, "sort": "desc",
    })
    out = set()
    for t in res.get("result", []) if res.get("status") == "1" else []:
        if (t.get("from", "") or "").lower() != wl:
            continue
        to = (t.get("to", "") or "").lower()
        if to and to != bridge and to != wl and int(t.get("value", 0) or 0) > 0:
            out.add(to)
    return out


def target_l1_profile(target: str) -> dict:
    """Target's L1 fingerprint for clustering: first funder + outbound addresses.
    Outbound addresses come from already-collected l1_transactions (no API calls);
    first funder needs one Etherscan lookup."""
    config = load_config()
    bridge = config["hl_bridge_contract"].lower()
    tl = target.lower()

    out_addrs = set()
    for t in load_all_records(str(DATA_DIR / "l1_transactions")):
        if (t.get("from", "") or "").lower() != tl:
            continue
        to = (t.get("to", "") or "").lower()
        if to and to != bridge and to != tl and int(t.get("value", 0) or 0) > 0:
            out_addrs.add(to)

    return {
        "first_funder": get_first_funder(target),
        "out_addrs": out_addrs,
    }


def check_candidate(wallet: str, target: str, profile: dict) -> dict:
    """Run both heuristics against one candidate and return linkage evidence."""
    config = load_config()
    excluded = set(config.get("excluded_addresses", [])) | set(config.get("known_self_wallets", []))
    return compute_linkage(
        wallet,
        get_first_funder(wallet),
        get_outbound_usdc_addresses(wallet),
        target,
        profile.get("first_funder"),
        profile.get("out_addrs", set()),
        excluded,
    )
