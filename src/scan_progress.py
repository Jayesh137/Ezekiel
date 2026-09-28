"""Fair work ordering that survives partial scanner runs and checkpoint restore."""

from src.discovery_store import DiscoveryStore


def interleave_resume(ordered, pending, key=lambda row: row):
    """Alternate unfinished histories and fresh work, preserving age order."""
    waiting = [row for row in ordered if key(row) in pending]
    fresh = [row for row in ordered if key(row) not in pending]
    out = []
    while waiting or fresh:
        resume = waiting and (len(out) % 2 == 0 or not fresh)
        out.append((waiting if resume else fresh).pop(0))
    return out


def priority_order(rows, attempts, limit=80, pending=()):
    ordered = sorted(rows, key=lambda wallet: (attempts.get(wallet, 0), wallet))
    explore = interleave_resume([w for w in ordered if rows[w].get('discovery')], pending)
    factual = interleave_resume([w for w in ordered if not rows[w].get('discovery')], pending)
    out = []
    while (factual or explore) and len(out) < limit:
        # One exploration slot in every five, including the first batch.
        take_explore = explore and (len(out) % 5 == 0 or not factual)
        out.append((explore if take_explore else factual).pop(0))
    return out


class ScanProgress:
    def __init__(self, path, phase):
        self.path, self.key = path, 'scan_attempts:' + phase
        with DiscoveryStore(path) as store:
            self.attempts = store.meta(self.key, {})
            tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.pending = {r[0] for r in store.db.execute('SELECT wallet FROM fill_progress')} if 'fill_progress' in tables else set()

    def mark(self, wallet, at_ms):
        self.attempts[wallet] = at_ms
        self.attempts = dict(sorted(self.attempts.items(), key=lambda p: p[1], reverse=True)[:10000])
        with DiscoveryStore(self.path) as store:
            store.set_meta(self.key, self.attempts)
