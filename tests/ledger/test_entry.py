import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from mor.audit import Actor, ActorKind
from mor.ledger.account import MerchantPayable, PspClearing
from mor.ledger.entry import Line, UnbalancedEntry, build_entry, credit, debit
from mor.money.amount import Money
from mor.money.currency import Currency


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
SYSTEM = Actor(ActorKind.SYSTEM, "test")
CLEARING = PspClearing(uuid.uuid7())
MERCHANT = MerchantPayable(uuid.uuid7())


def _sgd(amount_minor: int) -> Money:
    return Money(amount_minor, Currency.SGD)


def test_debit_is_positive_and_credit_negative() -> None:
    assert debit(CLEARING, _sgd(500)).amount == _sgd(500)
    assert credit(MERCHANT, _sgd(500)).amount == _sgd(-500)


def test_debit_or_credit_of_zero_or_negative_is_a_bug() -> None:
    for amount in [_sgd(0), _sgd(-1)]:
        with pytest.raises(UnbalancedEntry):
            debit(CLEARING, amount)
        with pytest.raises(UnbalancedEntry):
            credit(CLEARING, amount)


def test_balanced_entry_gets_a_time_ordered_id() -> None:
    lines = [debit(CLEARING, _sgd(500)), credit(MERCHANT, _sgd(500))]
    first = build_entry("k1", "test", NOW, SYSTEM, lines, NOW)
    second = build_entry("k2", "test", NOW, SYSTEM, lines, NOW)
    assert first.id.version == 7
    assert first.id < second.id


def test_unbalanced_entry_is_a_bug() -> None:
    lines = [debit(CLEARING, _sgd(500)), credit(MERCHANT, _sgd(499))]
    with pytest.raises(UnbalancedEntry, match="off by 1"):
        build_entry("k", "test", NOW, SYSTEM, lines, NOW)


def test_entry_must_balance_in_each_currency_separately() -> None:
    # Equal numbers in two currencies are not a balance.
    lines = [
        debit(CLEARING, _sgd(500)),
        credit(MERCHANT, Money(500, Currency.MYR)),
    ]
    with pytest.raises(UnbalancedEntry):
        build_entry("k", "test", NOW, SYSTEM, lines, NOW)


def test_one_line_or_a_zero_line_is_a_bug() -> None:
    with pytest.raises(UnbalancedEntry):
        build_entry("k", "test", NOW, SYSTEM, [debit(CLEARING, _sgd(1))], NOW)
    zero = Line(MERCHANT, _sgd(0))
    lines = [debit(CLEARING, _sgd(1)), credit(MERCHANT, _sgd(1)), zero]
    with pytest.raises(UnbalancedEntry):
        build_entry("k", "test", NOW, SYSTEM, lines, NOW)


def test_naive_or_non_utc_timestamps_are_refused() -> None:
    lines = [debit(CLEARING, _sgd(500)), credit(MERCHANT, _sgd(500))]
    naive = datetime(2026, 11, 16, 9, 30)  # noqa: DTZ001 - the input refused
    bangkok = datetime(2026, 11, 16, 16, 30, tzinfo=timezone(timedelta(hours=7)))
    for moment in [naive, bangkok]:
        with pytest.raises(ValueError):
            build_entry("k", "test", moment, SYSTEM, lines, NOW)
        with pytest.raises(ValueError):
            build_entry("k", "test", NOW, SYSTEM, lines, moment)
