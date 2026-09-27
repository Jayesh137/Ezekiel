"""Bounded public reads with response-weight pacing and graceful throttling."""

import time
from contextvars import ContextVar

_active = ContextVar('hl_read_budget', default=None)
LIGHT = {'l2Book', 'allMids', 'clearinghouseState', 'orderStatus', 'spotClearinghouseState', 'exchangeStatus'}
ROW_WEIGHTED = {'recentTrades', 'historicalOrders', 'userFills', 'userFillsByTime',
                'fundingHistory', 'userFunding', 'nonUserFundingUpdates', 'twapHistory',
                'userTwapSliceFills', 'userTwapSliceFillsByTime', 'delegatorHistory',
                'delegatorRewards', 'validatorStats'}


def current_budget():
    return _active.get()


class ReadBudget:
    def __init__(self, seconds=900, weight_per_minute=600, *, clock=None, sleep=None):
        self.clock, self.sleep = clock or time.monotonic, sleep or time.sleep
        self.started = self.clock()
        self.deadline = self.started + max(0, seconds)
        self.next_request = self.started
        self.seconds_per_weight = 60 / max(1, weight_per_minute)
        self.weight = self.calls = 0
        self.failed_reads = 0
        self.incomplete_histories = 0
        self.stopped_reason = None
        self.attempted_wallets = set()
        self.phase_coverage = {}

    def __enter__(self):
        self.token = _active.set(self)
        return self

    def __exit__(self, *args):
        _active.reset(self.token)

    def can_continue(self):
        if self.clock() >= self.deadline and not self.stopped_reason:
            self.stopped_reason = 'time_budget'
        return self.stopped_reason is None

    def before(self, body):
        if not self.can_continue():
            return False
        wait = max(0, self.next_request - self.clock())
        if self.clock() + wait >= self.deadline:
            self.stopped_reason = 'time_budget'
            return False
        if wait:
            self.sleep(wait)
        kind = body.get('type')
        weight = 2 if kind in LIGHT else 60 if kind == 'userRole' else 20
        self.weight += weight
        self.calls += 1
        self.next_request = self.clock() + weight * self.seconds_per_weight
        return True

    def after(self, body, data):
        if isinstance(data, list) and body.get('type') in ROW_WEIGHTED:
            weight = (len(data) + 19) // 20
            self.weight += weight
            self.next_request += weight * self.seconds_per_weight

    def timeout(self):
        # Bound the current request as well as the time between requests.
        remaining = max(.2, self.deadline - self.clock())
        return min(5, remaining / 2), min(15, remaining / 2)

    def report(self):
        return {'calls': self.calls, 'weight': self.weight, 'failed_reads': self.failed_reads,
                'incomplete_histories': self.incomplete_histories,
                'elapsed_seconds': round(self.clock() - self.started, 2),
                'stopped_reason': self.stopped_reason, 'phases': self.phase_coverage,
                'status': 'partial' if self.stopped_reason or self.failed_reads or self.incomplete_histories
                or any(v.get('deferred', 0) for v in self.phase_coverage.values()) else 'ok'}
