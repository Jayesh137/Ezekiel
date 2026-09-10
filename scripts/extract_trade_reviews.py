#!/usr/bin/env python3
"""Carve the trade-review images out of the PDF, and index them.

`research/Trade Reviews.pdf` is 119 pages with a 137-character text layer: the
content IS the images, so the PDF alone is unreadable without a renderer. This
produces the usable form, and both are kept — the PDF as the untouched source,
the JPEGs as the thing a person or a model can actually look at.

No dependency: images stored with /DCTDecode ARE JPEGs, so they are carved
between the SOI and EOI markers rather than decoded. `pdftoppm` and `pdfimages`
are not available in this environment; only `pdftotext` is, which returns almost
nothing here.

Deterministic, so re-running reproduces the same files and the committed images
can be checked against their source.
"""

import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

PDF = Path("research/Trade Reviews.pdf")
OUT = Path("research/trade_review_images")
INDEX = Path("research/trade_review_index.json")

# Avatars and UI icons are small; tweet screenshots and charts are not. Below
# this, an image carries no readable content.
MIN_BYTES = 15_000


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height from the JPEG's own SOF marker."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return 0, 0


def classify(width: int, height: int) -> str:
    """Tweet screenshots are wide and short; charts are tall or square."""
    ratio = (width / height) if height else 0
    if ratio >= 2.5:
        return "tweet_screenshot"
    if ratio < 1.2:
        return "chart"
    return "mixed"


def extract(pdf: Path = PDF, out: Path = OUT) -> list[dict]:
    raw = pdf.read_bytes()
    out.mkdir(parents=True, exist_ok=True)
    items, kept = [], 0
    for match in re.finditer(rb"/DCTDecode", raw):
        start = raw.find(b"stream", match.end())
        if start < 0:
            continue
        start = raw.find(b"\xff\xd8", start, start + 3000)     # JPEG SOI
        if start < 0:
            continue
        end = raw.find(b"\xff\xd9", start)                     # JPEG EOI
        if end < 0:
            continue
        data = raw[start:end + 2]
        if len(data) < MIN_BYTES:
            continue
        kept += 1
        name = f"img{kept:03d}.jpg"
        (out / name).write_bytes(data)
        w, h = jpeg_dimensions(data)
        items.append({"file": name, "width": w, "height": h,
                      "bytes": len(data), "kind": classify(w, h),
                      "reviewed": False, "notes": None})
    return items


def main() -> int:
    if not PDF.exists():
        print(f"[extract] missing {PDF}")
        return 1
    # Preserve review notes across re-runs: they are the expensive part.
    previous = {}
    if INDEX.exists():
        try:
            with open(INDEX) as f:
                previous = {i["file"]: i for i in json.load(f).get("images", [])}
        except (OSError, ValueError):
            previous = {}

    items = extract()
    for item in items:
        old = previous.get(item["file"])
        if old and old.get("reviewed"):
            item["reviewed"] = True
            item["notes"] = old.get("notes")

    counts = {k: sum(1 for i in items if i["kind"] == k)
              for k in ("tweet_screenshot", "chart", "mixed")}
    INDEX.write_text(json.dumps({
        "source": str(PDF),
        "extracted": "2026-09-10",
        "total": len(items),
        "_how_to_use": [
            "The PDF has a 137-character text layer; the content IS the images.",
            "Tweet screenshots here are NOT all in gcr_archive.txt — 'fade listing",
            "pumps' (IOTX/TRU/CLV/MNGO/AXS) appears only here, and reading it",
            "corrected a finding about the target.",
            "Review incrementally: set reviewed=true and fill notes. Re-running",
            "this script preserves both.",
        ],
        "counts": counts,
        "images": items,
    }, indent=2), encoding="utf-8")
    reviewed = sum(1 for i in items if i["reviewed"])
    print(f"[extract] {len(items)} image(s): {counts}")
    print(f"[extract] {reviewed} already reviewed, notes preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
