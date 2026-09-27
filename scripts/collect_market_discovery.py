"""Collect public observations without credentials or paid data services."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discovery_store import DiscoveryStore
from src.market_discovery import collect_once, collect_stream, import_jsonl
from src.utils import DATA_DIR, atomic_write_json, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="bounded snapshot polling (default)")
    mode.add_argument("--stream", action="store_true", help="optional websocket collection")
    mode.add_argument("--import-jsonl", type=Path, help="local trade rows or trade-channel envelopes")
    parser.add_argument("--db", type=Path, default=DATA_DIR / ".local" / "discovery.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR / "discovery")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--max-markets", type=int, default=12)
    parser.add_argument("--markets", nargs="+", help="preferred markets including HIP-3 symbols")
    args = parser.parse_args()
    config = load_config()
    options = config.setdefault("discovery", {})
    options["max_markets"] = args.max_markets
    if args.markets:
        options["markets"] = args.markets
    elif not options.get("markets"):
        try:
            fp = json.loads((DATA_DIR.parent / "profile" / "fingerprint.json").read_text())
            options["markets"] = fp.get("asset_preferences", {}).get("coins_traded") or ["BTC", "ETH", "HYPE"]
        except (OSError, ValueError):
            pass
    with DiscoveryStore(args.db) as store:
        if args.import_jsonl:
            result = import_jsonl(args.import_jsonl, store)
        elif args.stream:
            result = collect_stream(config, store, seconds=args.seconds)
        else:
            result = collect_once(config, store)
        store.prune(int(time.time() * 1000) - 30 * 86400_000)
        summary = store.export_summary()
        summary["last_run"] = result
        atomic_write_json(args.output_dir / "latest.json", summary)
        print(json.dumps({**{k: v for k, v in summary.items() if k not in ("candidates", "coverage", "last_run")},
                          "status": result.get("status"), "errors": result.get("errors", [])}))
        if result.get("status") == "error":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
