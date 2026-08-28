import pytest

from src.chain.budget import BudgetExhausted, CallBudget


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_spending_counts_calls():
    b = CallBudget(max_calls=3, seconds=100, clock=FakeClock())
    b.spend()
    b.spend()
    assert b.calls_used == 2
    assert b.remaining_calls() == 1
    assert b.exhausted_reason() is None


def test_exhausting_calls_raises_and_names_the_reason():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    assert not b.can_spend()
    assert b.exhausted_reason() == "call_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_deadline_exhausts_independently_of_call_count():
    clock = FakeClock()
    b = CallBudget(max_calls=100, seconds=10, clock=clock)
    b.spend()
    clock.t = 11.0
    assert not b.can_spend()
    assert b.exhausted_reason() == "time_budget"
    with pytest.raises(BudgetExhausted):
        b.spend()


def test_calls_used_survives_exhaustion_so_partial_work_is_reportable():
    b = CallBudget(max_calls=1, seconds=100, clock=FakeClock())
    b.spend()
    with pytest.raises(BudgetExhausted):
        b.spend()
    assert b.calls_used == 1
