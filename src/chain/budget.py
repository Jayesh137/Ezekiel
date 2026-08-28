"""A hard ceiling on API calls and wall clock for one collection run.

The collection jobs run under a 10-minute GitHub Actions timeout against a
rate-limited free API tier. Every reader takes a budget and checks it before
spending, so a wallet with a very long history degrades into a partial sweep
that reports where it stopped, rather than a cancelled job that reports
nothing. `calls_used` deliberately survives exhaustion: the diagnostics are the
point.
"""

import time


class BudgetExhausted(RuntimeError):
    """Raised when a caller tries to spend past the ceiling."""


class CallBudget:
    __slots__ = ("max_calls", "seconds", "_clock", "_started", "calls_used")

    def __init__(self, max_calls: int, seconds: float, clock=time.monotonic):
        self.max_calls = int(max_calls)
        self.seconds = float(seconds)
        self._clock = clock
        self._started = clock()
        self.calls_used = 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def exhausted_reason(self) -> str | None:
        if self.calls_used >= self.max_calls:
            return "call_budget"
        if self.elapsed() >= self.seconds:
            return "time_budget"
        return None

    def can_spend(self, n: int = 1) -> bool:
        if self.elapsed() >= self.seconds:
            return False
        return self.calls_used + n <= self.max_calls

    def spend(self, n: int = 1) -> None:
        if not self.can_spend(n):
            raise BudgetExhausted(self.exhausted_reason() or "call_budget")
        self.calls_used += n
