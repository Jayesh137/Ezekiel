"""Build offline route investigations from stored transfers and explicit action imports."""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.authority_history import index_authority
from src.candidate_registry import observe_candidate
from src.discovery_store import DiscoveryStore
from src.route_binding import bound_decode
from src.route_index import index_routes, resolve_source_routes
from src.utils import DATA_DIR, atomic_write_json, load_config


def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def run(config, data_dir=DATA_DIR, *, actions=None, route_input=None, output_dir=None, db_path=None,
        resolve_sources=False):
    from src.chain.collect import decode_records, substrate_files
    data_dir = Path(data_dir)
    cluster = {config["target_wallet"].lower(), *(w.lower() for w in config.get("known_self_wallets", []))}
    if route_input is None:
        records = {}
        read_errors = []
        for chain_dir in (data_dir / "transfers").glob("*"):
            if not chain_dir.is_dir():
                continue
            for path in substrate_files(chain_dir):
                try:
                    for row in decode_records(path):
                        if isinstance(row, dict) and row.get("id") and not row.get("spam"):
                            if row.get("src", "").lower() in cluster or row.get("dst", "").lower() in cluster:
                                records[row["id"]] = row
                except (OSError, ValueError, EOFError) as exc:
                    read_errors.append(f"{path.name}: {exc}")
        decodes = read(data_dir / "labels" / "bridge_decodes.json", {})
        circle = read(data_dir / "circle_flows" / "latest.json", {})
        circle_events = circle.get("cluster_flows", []) + circle.get("findings", [])
    else:
        read_errors = []
        records = {str(i): r for i, r in enumerate(route_input.get("records", []))}
        decodes, circle_events = route_input.get("bridge_decodes", {}), route_input.get("circle_events", [])
    now_ms = int(time.time() * 1000)
    with DiscoveryStore(db_path or data_dir / ".local" / "discovery.sqlite3") as store:
        circle_events += store.observations("circle")
        if actions:
            store.ingest_observations("authority_actions", actions, now_ms)
        pending = read(data_dir / 'agent_links' / 'pending_observations.json', [])
        if pending:
            store.ingest_observations('authority_actions', pending, now_ms)
        authority = index_authority(store.observations("authority_actions"), as_of_ms=now_ms)
        transfers = list(records.values())
        source_reads = {"queries": 0, "errors": [], "enabled": resolve_sources}
        if resolve_sources:
            transfers, decodes, source_reads = resolve_source_routes(transfers, decodes, store)
            # Small immutable transfer-level proofs are also available to the
            # offline economic reconciler, without opening a writable store.
            bindings = {row['id']: row['route_decode'] for row in transfers if bound_decode(row)}
            atomic_write_json(data_dir / 'routes' / 'bindings.json', bindings)
        else:
            bindings = read(data_dir / 'routes' / 'bindings.json', {})
            transfers = [{**row, 'route_decode': row.get('route_decode') or bindings.get(row.get('id'))}
                         for row in transfers]
        routes = index_routes(transfers, decodes, circle_events, cluster)
        for row in routes["discoveries"]:
            observe_candidate(row["wallet"], {**row, "status": "ok"}, data_dir)
        for row in authority["shared_authority"]:
            if cluster.intersection(row["accounts"]):
                for wallet in set(row["accounts"]) - cluster:
                    observe_candidate(wallet, {**row, "source": "authority_history", "status": "ok",
                                               "event_id": ":".join(row["parent_event_ids"]), "positive": True}, data_dir)
        report = {**routes, "computed_at_ms": now_ms, "authority": authority,
                  "read_errors": read_errors,
                  "source_message_reads": source_reads,
                  "retention": {"circle_rows_read": len(circle_events), "store_read_limit": 100_000}}
        for key in ("routes", "unresolved", "discoveries"):
            report[key] = sorted(report[key], key=lambda r: r.get('ts_ms') or 0, reverse=True)[:1000]
        report["authority"] = {**authority, "intervals": authority["intervals"][:1000],
                               "shared_authority": authority["shared_authority"][:1000]}
        atomic_write_json(Path(output_dir or data_dir / "routes") / "latest.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actions", type=Path, help="JSON array of successful, dated authority actions")
    parser.add_argument("--routes", type=Path, help="JSON object with records, bridge_decodes, circle_events")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--resolve-sources", action="store_true", help="up to 20 free Circle source-message lookups")
    args = parser.parse_args()
    report = run(load_config(), args.data_dir, actions=read(args.actions, []) if args.actions else None,
                 route_input=read(args.routes, {}) if args.routes else None,
                 output_dir=args.output_dir, db_path=args.db, resolve_sources=args.resolve_sources)
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
