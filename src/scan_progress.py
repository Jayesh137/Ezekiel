"""Fair work ordering that survives partial scanner runs and checkpoint restore."""

from src.discovery_store import DiscoveryStore


def priority_order(rows, attempts, limit=80):
    ordered = sorted(rows, key=lambda wallet: (attempts.get(wallet, 0), wallet))
    explore = [w for w in ordered if rows[w].get('discovery')]
    factual = [w for w in ordered if not rows[w].get('discovery')]
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

    def mark(self, wallet, at_ms):
        self.attempts[wallet] = at_ms
        self.attempts = dict(sorted(self.attempts.items(), key=lambda p: p[1], reverse=True)[:10000])
        with DiscoveryStore(self.path) as store:
            store.set_meta(self.key, self.attempts)
