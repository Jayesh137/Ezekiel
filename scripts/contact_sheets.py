#!/usr/bin/env python3
"""Tile the trade-review images into contact sheets for triage.

Reading 406 screenshots one at a time costs more context than a session has, and
pixel heuristics do not separate them: blotters run from one row to nine, light
theme (Binance) to dark (FTX), so brightness, colour and aspect all fail. A
filter tuned on three light Binance blotters missed a dark FTX one entirely.

Looking is what works. A grid of thumbnails is enough to tell a position table
from a tweet from a chart, and only the tables then need reading at full size.

Not part of any detection vector — a triage tool for a human or a model working
through `research/trade_review_index.json`. Pillow is imported lazily so the
rest of the repo, and CI, never depend on it.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

IMAGES = Path("research/trade_review_images")
INDEX = Path("research/trade_review_index.json")
DEFAULT_OUT = Path("research/contact_sheets")

CELL = (520, 380)
COLS = 4
ROWS = 5
LABEL_H = 22
PAD = 6


def _load_index() -> list[dict]:
    try:
        with open(INDEX) as f:
            return json.load(f).get("images", [])
    except (OSError, ValueError):
        return []


def build(files: list[str], out: Path, cols: int = COLS, rows: int = ROWS) -> list[Path]:
    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    per = cols * rows
    written = []
    for page, start in enumerate(range(0, len(files), per), 1):
        chunk = files[start:start + per]
        cw, ch = CELL
        sheet = Image.new("RGB",
                          (cols * (cw + PAD) + PAD,
                           rows * (ch + LABEL_H + PAD) + PAD),
                          (24, 24, 28))
        draw = ImageDraw.Draw(sheet)
        for n, name in enumerate(chunk):
            path = IMAGES / name
            if not path.exists():
                continue
            col, row = n % cols, n // cols
            x = PAD + col * (cw + PAD)
            y = PAD + row * (ch + LABEL_H + PAD)
            draw.text((x + 2, y + 5), name, fill=(235, 235, 120))
            try:
                im = Image.open(path).convert("RGB")
            except OSError:
                continue
            im.thumbnail((cw, ch))
            # White plate behind each thumbnail so a dark screenshot's edges stay
            # visible against a dark sheet.
            sheet.paste(im, (x + (cw - im.width) // 2,
                             y + LABEL_H + (ch - im.height) // 2))
        target = out / f"sheet{page:02d}.jpg"
        sheet.save(target, quality=82)
        written.append(target)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--all", action="store_true",
                    help="include images already marked reviewed")
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--rows", type=int, default=ROWS)
    args = ap.parse_args()

    images = _load_index()
    if not images:
        print(f"[sheets] no index at {INDEX}")
        return 1
    files = [i["file"] for i in images if args.all or not i.get("reviewed")]
    if not files:
        print("[sheets] every image is reviewed; pass --all to rebuild anyway")
        return 0
    try:
        written = build(files, args.out, args.cols, args.rows)
    except ImportError:
        print("[sheets] needs Pillow: .venv/Scripts/python -m pip install pillow")
        return 1
    print(f"[sheets] {len(files)} image(s) -> {len(written)} sheet(s) in {args.out}")
    for p in written:
        print(f"[sheets]   {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
