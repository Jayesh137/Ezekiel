# src/links.py
"""Explorer links for generated messages (emails, issue bodies).

Mirrors dashboard/src/lib/api.js so an address in an alert and the same address
on the dashboard always resolve to the same page. Wallet addresses go to
Hypurrscan.

Pure: no I/O, no config, no network.
"""

import re
from urllib.parse import quote

HYPURRSCAN = "https://hypurrscan.io"

# A complete EVM address. A shortened label like "0x45d2...4029" fails this by
# design — a link must never be built from what the reader was shown.
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_address(value: object) -> bool:
    """True only for a complete, well-formed address."""
    return isinstance(value, str) and bool(_ADDRESS.match(value.strip()))


def address_url(address: str) -> str | None:
    """Hypurrscan address page, or None when the value cannot be linked.

    Casing is preserved so a checksummed address stays checksummed.
    """
    if not is_address(address):
        return None
    return f"{HYPURRSCAN}/address/{quote(address.strip(), safe='')}"


def address_path(addresses: list, indent: str = "    ") -> str:
    """An ordered hop path, one wallet per line, each with its Hypurrscan page.

    Kept here rather than in the alert body so a hop in an email resolves to the
    same page as the same hop on the dashboard.
    """
    out = []
    for i, addr in enumerate(addresses or []):
        arrow = "" if i == 0 else "-> "
        url = address_url(addr)
        suffix = f"\n{indent}   {url}" if url else ""
        out.append(f"{indent}{arrow}{addr}{suffix}")
    return "\n".join(out) if out else f"{indent}(unknown)"


def address_line(address: str, label: str = "Wallet") -> str:
    """One labelled line for a plain-text message body.

    Falls back to the bare value when it cannot be linked, so a malformed
    address is still reported rather than silently dropped.
    """
    url = address_url(address)
    if url is None:
        return f"{label}: {address or '(unknown)'}"
    return f"{label}: {address}\n{label} on Hypurrscan: {url}"
