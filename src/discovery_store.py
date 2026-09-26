"""Local, transactional public observations. Wallet involvement is not ownership."""

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from src.candidate_registry import valid_wallet


class DiscoveryStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS market_events (
                id TEXT PRIMARY KEY, coin TEXT, ts INTEGER, raw TEXT, digest TEXT);
            CREATE INDEX IF NOT EXISTS market_event_time ON market_events(ts);
            CREATE TABLE IF NOT EXISTS market_participants (
                event_id TEXT, wallet TEXT, ts INTEGER, PRIMARY KEY(event_id,wallet));
            CREATE INDEX IF NOT EXISTS participant_wallet ON market_participants(wallet,ts);
            CREATE TABLE IF NOT EXISTS discovered_wallets (
                wallet TEXT PRIMARY KEY, first_seen_ms INTEGER, last_seen_ms INTEGER,
                trade_count INTEGER, notional_usd REAL, markets TEXT,
                last_checked_ms INTEGER DEFAULT 0, next_check_ms INTEGER DEFAULT 0,
                read_status TEXT);
            CREATE TABLE IF NOT EXISTS discovery_excluded (wallet TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS discovery_coverage (
                id INTEGER PRIMARY KEY, source TEXT, observed_at_ms INTEGER, data TEXT);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM discovery_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO discovery_meta VALUES (?,?)", (key, json.dumps(value)))

    def set_exclusions(self, wallets):
        valid = []
        for wallet in wallets:
            try:
                valid.append((valid_wallet(wallet),))
            except ValueError:
                continue
        with self.db:
            self.db.execute("DELETE FROM discovery_excluded")
            self.db.executemany("INSERT OR IGNORE INTO discovery_excluded VALUES (?)", valid)

    def ingest_trades(self, trades: list[dict], observed_at_ms: int) -> dict:
        result = dict(inserted=0, duplicates=0, rejected=0, conflicts=0, outside_retention=0)
        watermark = self.meta("pruned_through_ms", -1)
        with self.db:
            for row in trades:
                try:
                    coin = row["coin"]
                    ts, tid = int(row["time"]), int(row["tid"])
                    if (not isinstance(coin, str) or not 0 < len(coin) <= 80
                            or ts <= 0 or ts > observed_at_ms + 60_000 or tid < 0):
                        raise ValueError("invalid identity")
                    px, sz = float(row["px"]), float(row["sz"])
                    if not math.isfinite(px * sz) or px <= 0 or sz <= 0:
                        raise ValueError("invalid size or price")
                    if not isinstance(row["users"], list) or len(row["users"]) != 2:
                        raise ValueError("expected buyer and seller")
                    users = [valid_wallet(w) for w in row["users"]]
                    if "0x" + "0" * 40 in users or row.get("side") not in ("A", "B"):
                        raise ValueError("invalid participants or side")
                except (KeyError, TypeError, ValueError, OverflowError):
                    result["rejected"] += 1
                    continue
                if ts <= watermark:
                    result["outside_retention"] += 1
                    continue
                raw = json.dumps({**row, "users": users, "time": ts, "tid": tid}, sort_keys=True)
                # Hash only protocol identity/content; local import annotations
                # must not turn an identical execution into a conflict.
                digest = hashlib.sha256(json.dumps([coin, ts, tid, px, sz, users, row["side"]]).encode()).hexdigest()
                event_id = json.dumps([coin, ts, tid])
                prior = self.db.execute("SELECT digest FROM market_events WHERE id=?", (event_id,)).fetchone()
                if prior:
                    result["duplicates" if prior[0] == digest else "conflicts"] += 1
                    continue
                self.db.execute("INSERT INTO market_events VALUES (?,?,?,?,?)", (event_id, coin, ts, raw, digest))
                for wallet in dict.fromkeys(users):
                    self.db.execute("INSERT INTO market_participants VALUES (?,?,?)", (event_id, wallet, ts))
                    old = self.db.execute("SELECT markets FROM discovered_wallets WHERE wallet=?", (wallet,)).fetchone()
                    markets = json.loads(old[0]) if old else {}
                    if coin in markets or len(markets) < 100:
                        markets[coin] = markets.get(coin, 0) + 1
                    # Spot quote assets vary. Keep their raw price/size but don't
                    # pretend their notional is USD without quote conversion.
                    notional = 0 if coin.startswith("@") else px * sz
                    self.db.execute("""INSERT INTO discovered_wallets
                        (wallet,first_seen_ms,last_seen_ms,trade_count,notional_usd,markets)
                        VALUES (?,?,?,1,?,?) ON CONFLICT(wallet) DO UPDATE SET
                        first_seen_ms=min(first_seen_ms,excluded.first_seen_ms),
                        last_seen_ms=max(last_seen_ms,excluded.last_seen_ms),
                        trade_count=trade_count+1, notional_usd=notional_usd+excluded.notional_usd,
                        markets=excluded.markets""", (wallet, ts, ts, notional, json.dumps(markets)))
                result["inserted"] += 1
        return result

    def candidates(self, limit: int = 500, now_ms: int | None = None) -> list[dict]:
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        rows = self.db.execute("""SELECT * FROM discovered_wallets WHERE next_check_ms<=?
            AND last_seen_ms>=? AND wallet NOT IN (SELECT wallet FROM discovery_excluded)
            ORDER BY last_checked_ms ASC,last_seen_ms DESC,wallet LIMIT ?""",
            (now_ms, now_ms - 30 * 86400_000, max(0, min(limit, 5000))))
        return [{**dict(r), "markets": json.loads(r["markets"]), "source": "public_trades"} for r in rows]

    def mark_checked(self, wallet, now_ms, status):
        with self.db:
            self.db.execute("UPDATE discovered_wallets SET last_checked_ms=?,next_check_ms=?,read_status=? WHERE wallet=?",
                            (now_ms, now_ms + (3600_000 if status == "error" else 6 * 3600_000), status, wallet.lower()))

    def wallet_trades(self, wallet, start_ms=0, end_ms=None, limit=10_000):
        rows = self.db.execute("""SELECT e.raw FROM market_events e JOIN market_participants p
            ON p.event_id=e.id WHERE p.wallet=? AND p.ts>=? AND p.ts<=? ORDER BY p.ts,e.id LIMIT ?""",
            (valid_wallet(wallet), start_ms, end_ms or 2**63 - 1, min(limit, 50_000)))
        return [json.loads(r[0]) for r in rows]

    def record_observation(self, source, observed_at_ms, **data):
        with self.db:
            self.db.execute("INSERT INTO discovery_coverage(source,observed_at_ms,data) VALUES (?,?,?)",
                            (source, observed_at_ms, json.dumps(data)))
            self.db.execute("DELETE FROM discovery_coverage WHERE id NOT IN "
                            "(SELECT id FROM discovery_coverage ORDER BY id DESC LIMIT 1000)")

    def coverage(self) -> dict:
        rows = self.db.execute("SELECT source,observed_at_ms,data FROM discovery_coverage ORDER BY id DESC LIMIT 100")
        return {"continuous": False, "scope": "observations only; gaps and offline periods may contain unseen trades",
                "pruned_through_ms": self.meta("pruned_through_ms"),
                "observations": [{"source": r[0], "observed_at_ms": r[1], **json.loads(r[2])} for r in reversed(list(rows))]}

    def prune(self, before_ms, max_events=500_000):
        extra = self.db.execute("SELECT ts FROM market_events ORDER BY ts DESC LIMIT 1 OFFSET ?",
                                (max(1, max_events),)).fetchone()
        cutoff = max(before_ms - 1, extra[0] if extra else -1, self.meta("pruned_through_ms", -1))
        with self.db:
            self.db.execute("DELETE FROM market_participants WHERE ts<=?", (cutoff,))
            self.db.execute("DELETE FROM market_events WHERE ts<=?", (cutoff,))
            self.db.execute("INSERT OR REPLACE INTO discovery_meta VALUES ('pruned_through_ms',?)", (json.dumps(cutoff),))
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def export_summary(self) -> dict:
        return {"wallets_observed": self.db.execute("SELECT count(*) FROM discovered_wallets").fetchone()[0],
                "events_retained": self.db.execute("SELECT count(*) FROM market_events").fetchone()[0],
                "coverage": self.coverage(), "candidates": self.candidates(limit=100),
                "identity_claim": False}
