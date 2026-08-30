# src/linkage.py
"""L1 wallet-clustering heuristics used to confirm a candidate is the same person.

Two research-backed signals, applied to promising behavioral candidates:

1. Shared funding source (first-funder heuristic). The first funds a fresh wallet
   receives usually come from a CEX withdrawal or a wallet the owner controls. If a
   candidate's first funder is the target itself, or the SAME funder the target was
   first funded by, that is a strong ownership link.

2. Address reuse (highest-confidence heuristic — cryptographic certainty). A CEX
   deposit address is unique to one account. If the candidate sends value to the
   SAME address the target sends to — on any chain, in any asset — they almost
   certainly share a CEX account. Labelled infrastructure (routers, wrapper
   contracts, exchange hot wallets) is excluded first: those receive from
   millions of unrelated people, so a shared one is coincidence, not ownership.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils import DATA_DIR, etherscan_get, load_config


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
            f"Sends funds to the same address as target (address reuse): "
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


def get_outbound_addresses(wallet: str, config: dict | None = None) -> set:
    """Every address this wallet has sent value to, on every collected chain,
    excluding known infrastructure.

    Reads the substrate rather than the API: src/chain/collect.py has already
    stored these, so widening the strongest linkage signal we have — a CEX
    deposit address belongs to exactly one account, so two wallets funding the
    same one are the same customer — from Arbitrum USDC to every chain and asset
    costs no calls at all.

    That signal only holds for a private deposit address. Widening the search
    to every chain and asset also widens the odds of landing on a router, a
    wrapper contract, or an exchange HOT wallet — infrastructure that receives
    from millions of unrelated people, where a shared destination is
    coincidence rather than evidence of common ownership. This result feeds a
    bonus the module calls "cryptographic certainty" and fires a standalone
    alert, so labelled infrastructure is excluded before it ever reaches that
    scoring step.
    """
    from src.chain.collect import records_for
    from src.chain.labels import load_registry, service_addresses

    config = config or load_config()
    wl = (wallet or "").lower()

    excluded = service_addresses(load_registry(DATA_DIR / "labels" / "entities.json"))
    excluded |= {a.lower() for a in config.get("known_service_addresses", [])}
    excluded.add(config["hl_bridge_contract"].lower())
    excluded.add(wl)

    out = set()
    for rec in records_for(wl):
        if (rec.get("src") or "").lower() != wl:
            continue
        usd = rec.get("amount_usd")
        if usd is None or float(usd) <= 0:
            continue
        dst = (rec.get("dst") or "").lower()
        if dst and dst not in excluded:
            out.add(dst)
    return out


def get_outbound_usdc_addresses(wallet: str, limit: int = 300) -> set:
    """Backwards-compatible alias. `limit` is unused: the substrate is complete,
    so there is no page to cap."""
    return get_outbound_addresses(wallet)


def target_l1_profile(target: str) -> dict:
    """Target's L1 fingerprint for clustering: first funder + outbound addresses.
    Outbound addresses come from the substrate, covering every collected chain
    and asset with no API calls; first funder needs one Etherscan lookup."""
    config = load_config()
    out_addrs = get_outbound_addresses(target, config)

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
