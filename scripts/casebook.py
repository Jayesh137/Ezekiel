#!/usr/bin/env python3
"""Read the casebook (spec 2026-10-08 §13). Read-only, except `sqlite`, which writes a
database file you choose (default data/.local/casebook.sqlite3, which git ignores).

    python scripts/casebook.py top [-n 25] [--all]
    python scripts/casebook.py show 0xADDRESS
    python scripts/casebook.py events [--since 2026-10-01] [--address 0x...] [-n 200]
    python scripts/casebook.py check
    python scripts/casebook.py sqlite [PATH]

The likelihood columns assume a 1-in-1,000 prior and mostly declared likelihood
ratios (data/casebook/latest.json "model" says which). They rank cases; they do not
prove ownership.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils
from src.casebook import cases as casefile
from src.casebook import model, store
from src.links import address_url

SCHEMA_SQL = """
CREATE TABLE cases (address TEXT PRIMARY KEY, rank INTEGER, known TEXT, ruling TEXT, excluded TEXT,
  p_central REAL, p_now REAL, p_ceiling REAL, central REAL, now REAL, ceiling REAL, cluster TEXT,
  tier TEXT, peak_tier TEXT, opened_at TEXT, last_change TEXT, on_hl INTEGER, total_value REAL,
  month_volume REAL, day_volume REAL, birth_ms INTEGER, headline TEXT);
CREATE TABLE evidence (address TEXT, key TEXT, kind TEXT, family TEXT, status TEXT, origin TEXT,
  first_seen TEXT, last_seen TEXT, seen_days INTEGER, strength REAL, peak_strength REAL,
  summary TEXT, facts TEXT, PRIMARY KEY (address, key));
CREATE TABLE tiers (address TEXT, day TEXT, tier TEXT);
CREATE TABLE probes (address TEXT, day TEXT, total_value REAL, month_volume REAL, day_volume REAL);
CREATE TABLE life (address TEXT, ts INTEGER, value REAL);
CREATE TABLE events (at TEXT, address TEXT, kind TEXT, origin TEXT, detail TEXT);
"""


def pct(p) -> str:
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return "-"
    if p >= 0.995:
        return ">99%"
    if p >= 0.1:
        return f"{p * 100:.0f}%"
    if p >= 0.01:
        return f"{p * 100:.1f}%"
    if p >= 0.001:
        return f"{p * 100:.2f}%"
    return "<0.1%"


def out(text: str = "") -> None:
    print(str(text).encode("ascii", "replace").decode("ascii"))


def cmd_top(args) -> int:
    index = store.read_index(args.data_dir)
    if not index:
        out("No casebook index (data/casebook/latest.json). Run scripts/update_casebook.py first.")
        return 1
    m, counts = index.get("model") or {}, index.get("counts") or {}
    prior = round(10 ** -(m.get("prior_log10_odds") or -3))
    out(f"Casebook {index.get('computed_at')}  model {m.get('version')}  prior 1 in {prior:,}")
    out(f"{counts.get('cases')} cases: {counts.get('unknown')} unknown, {counts.get('known')} known, "
        f"{counts.get('excluded')} excluded; {counts.get('on_hl')} on Hyperliquid")
    rows = [r for r in index.get("cases") or []
            if args.all or (r.get("rank") is not None and (r.get("hl") or {}).get("on_hl") is True)]
    for r in rows[: args.n]:
        rank = r.get("rank") if r.get("rank") is not None else ("K" if r.get("known") else "x")
        out(f"{str(rank):>4}  {r.get('address')}  {pct(r.get('p')):>6} "
            f"({pct(r.get('p_now'))}-{pct(r.get('p_ceiling'))})  {r.get('headline')}")
    return 0


def cmd_show(args) -> int:
    address = str(args.address).lower()
    try:
        path = store.case_path(address, args.data_dir)
    except ValueError:
        out(f"Not an address: {args.address}")
        return 1
    try:
        case = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        out(f"No case for {address}.")
        return 1
    except (OSError, ValueError) as exc:
        out(f"Case file unreadable ({exc}); it is left untouched for a human to look at.")
        return 1
    index = store.read_index(args.data_dir) or {}
    row = next((r for r in index.get("cases") or [] if r.get("address") == address), {})
    s = case.get("score") or {}
    out(f"Case {address}")
    out(f"  opened {case.get('opened_at')} ({case.get('origin')}) because: "
        f"{', '.join(case.get('opened_by') or [])}")
    if case.get("known"):
        out(f"  KNOWN: {case['known']}")
    if case.get("ruling"):
        out(f"  ruling: {json.dumps(case['ruling'], sort_keys=True)}")
    if case.get("excluded"):
        out(f"  EXCLUDED: {case['excluded'].get('reason')} since {case['excluded'].get('since')}")
    out(f"  rank {row.get('rank')}  likelihood {pct(s.get('p_central'))} (now {pct(s.get('p_now'))}, "
        f"ceiling {pct(s.get('p_ceiling'))}); log10 odds {s.get('central')} [{s.get('model')}]")
    if s.get("cluster"):
        out(f"  cluster {s['cluster']} ({s.get('cluster_size')} accounts); "
            f"its own evidence alone: {s.get('solo_central')}")
    out("  families (log10 likelihood ratio):")
    families = s.get("families") or {}
    for family, v in sorted(families.items(), key=lambda kv: -abs(kv[1].get("central") or 0)):
        out(f"    {family:<15} now {v.get('now', 0):+.2f}  central {v.get('central', 0):+.2f}  "
            f"ceiling {v.get('ceiling', 0):+.2f}")
    out("  evidence:")
    evidence = case.get("evidence") or {}
    for key, e in sorted(evidence.items(), key=lambda kv: kv[1].get("first_seen") or ""):
        out(f"    [{e.get('status')}] {key}: {e.get('summary')}")
        out(f"        first {e.get('first_seen')}  last {e.get('last_seen')}  days {e.get('seen_days')}  "
            f"origin {e.get('origin')}" + (f"  strongest: {e['peak_summary']}"
                                          if e.get("peak_summary") and e.get("peak_summary") != e.get("summary")
                                          else ""))
    r = case.get("roster") or {}
    out(f"  roster: tier {r.get('tier')}  peak {r.get('peak_tier')} ({r.get('peak_at')})  "
        f"last in roster {r.get('last_in_roster')}")
    out("    tiers by day: " + ", ".join(f"{d} {t}" for d, t in r.get("tiers") or []))
    for reason in r.get("reasons") or []:
        out(f"    reason ({r.get('reasons_at')}): {reason}")
    hl = case.get("hl") or {}
    out(f"  hyperliquid: on_hl {hl.get('on_hl')}  value {hl.get('total_value')}  30-day volume "
        f"{hl.get('month_volume')}  born {hl.get('birth_ms')}  probed {hl.get('probed_at')} "
        f"(ok {hl.get('probe_ok')})")
    out(f"  {address_url(address)}")
    return 0


def cmd_events(args) -> int:
    rows = store.read_events(args.data_dir, since=args.since, address=args.address)
    for e in rows[-args.n:]:
        out(f"{e.get('at')}  {e.get('address')}  {str(e.get('kind')):<20} "
            f"{json.dumps(e.get('detail') or {}, sort_keys=True)[:160]}")
    out(f"({len(rows)} events" + (f" since {args.since}" if args.since else "") + ")")
    return 0


def cmd_check(args) -> int:
    cases, unreadable = store.load_cases(args.data_dir)
    problems = [f"unreadable case {u['address']}: {u['error']}" for u in unreadable]
    for address, case in cases.items():
        if case.get("schema") != casefile.SCHEMA:
            problems.append(f"{address}: schema {case.get('schema')}")
        for key, e in (case.get("evidence") or {}).items():
            if e.get("kind") not in model.KINDS:
                problems.append(f"{address}: {key} has unknown kind {e.get('kind')}")
            if e.get("status") not in model.STATUSES:
                problems.append(f"{address}: {key} has unknown status {e.get('status')}")
    index = store.read_index(args.data_dir)
    if index is None:
        problems.append("latest.json is absent or unreadable")
    else:
        listed = {r.get("address") for r in index.get("cases") or []}
        missing = set(cases) - listed
        extra = listed - set(cases) - {u["address"] for u in unreadable}
        if missing:
            problems.append(f"{len(missing)} case(s) not in the index, e.g. {sorted(missing)[0]}")
        if extra:
            problems.append(f"{len(extra)} index row(s) without a case file, e.g. {sorted(extra)[0]}")
    for problem in problems[:50]:
        out(f"PROBLEM {problem}")
    out(f"{len(cases)} readable cases, {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_sqlite(args) -> int:
    target = Path(args.path) if args.path else Path(args.data_dir) / ".local" / "casebook.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    cases, unreadable = store.load_cases(args.data_dir)
    rows = {r.get("address"): r for r in (store.read_index(args.data_dir) or {}).get("cases") or []}
    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA_SQL)
        for address, case in sorted(cases.items()):
            r, s = rows.get(address, {}), case.get("score") or {}
            hl, roster = case.get("hl") or {}, case.get("roster") or {}
            con.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                address, r.get("rank"), case.get("known"), (case.get("ruling") or {}).get("verdict"),
                (case.get("excluded") or {}).get("reason"), s.get("p_central"), s.get("p_now"),
                s.get("p_ceiling"), s.get("central"), s.get("now"), s.get("ceiling"), s.get("cluster"),
                roster.get("tier"), roster.get("peak_tier"), case.get("opened_at"), case.get("last_change"),
                None if hl.get("on_hl") is None else int(bool(hl.get("on_hl"))), hl.get("total_value"),
                hl.get("month_volume"), hl.get("day_volume"), hl.get("birth_ms"), r.get("headline")))
            for key, e in (case.get("evidence") or {}).items():
                con.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    address, key, e.get("kind"), (model.KINDS.get(e.get("kind")) or {}).get("family"),
                    e.get("status"), e.get("origin"), e.get("first_seen"), e.get("last_seen"),
                    e.get("seen_days"), e.get("strength"), e.get("peak_strength"), e.get("summary"),
                    json.dumps(e.get("facts"), sort_keys=True)))
            con.executemany("INSERT INTO tiers VALUES (?,?,?)",
                            [(address, d, t) for d, t in roster.get("tiers") or []])
            con.executemany("INSERT INTO probes VALUES (?,?,?,?,?)",
                            [(address, *(list(p) + [None] * 4)[:4]) for p in hl.get("probes") or []])
            con.executemany("INSERT INTO life VALUES (?,?,?)",
                            [(address, ts, value) for ts, value in hl.get("life") or []])
        con.executemany("INSERT INTO events VALUES (?,?,?,?,?)",
                        [(e.get("at"), e.get("address"), e.get("kind"), e.get("origin"),
                          json.dumps(e.get("detail"), sort_keys=True))
                         for e in store.read_events(args.data_dir)])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, target)
    out(f"Wrote {target}: {len(cases)} cases ({len(unreadable)} unreadable, skipped)")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read the casebook.")
    parser.add_argument("--data-dir", type=Path, default=None, help="the data directory (default: data/)")
    sub = parser.add_subparsers(dest="command", required=True)
    top = sub.add_parser("top", help="the ranked suspects")
    top.add_argument("-n", type=int, default=25)
    top.add_argument("--all", action="store_true", help="every case, not only unknown ones on Hyperliquid")
    show = sub.add_parser("show", help="one case in full")
    show.add_argument("address")
    events = sub.add_parser("events", help="the change log")
    events.add_argument("--since")
    events.add_argument("--address")
    events.add_argument("-n", type=int, default=200)
    sub.add_parser("check", help="verify every file reads and the index matches the cases")
    sq = sub.add_parser("sqlite", help="export to a SQLite database")
    sq.add_argument("path", nargs="?")
    args = parser.parse_args(argv)
    args.data_dir = args.data_dir or utils.DATA_DIR
    commands = {"top": cmd_top, "show": cmd_show, "events": cmd_events, "check": cmd_check,
                "sqlite": cmd_sqlite}
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
