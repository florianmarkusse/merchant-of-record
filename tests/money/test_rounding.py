from fractions import Fraction

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from mor.money.amount import Money
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode, allocate, divide, round_fraction


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # value:                      2.5  -2.5  2.4  -2.4  2.6  -2.6  3.5
        (RoundingMode.HALF_EVEN, [2, -2, 2, -2, 3, -3, 4]),
        (RoundingMode.HALF_AWAY_FROM_ZERO, [3, -3, 2, -2, 3, -3, 4]),
        (RoundingMode.TOWARD_ZERO, [2, -2, 2, -2, 2, -2, 3]),
        (RoundingMode.AWAY_FROM_ZERO, [3, -3, 3, -3, 3, -3, 4]),
        (RoundingMode.FLOOR, [2, -3, 2, -3, 2, -3, 3]),
        (RoundingMode.CEILING, [3, -2, 3, -2, 3, -2, 4]),
    ],
)
def test_round_fraction_per_mode_including_negatives(
    mode: RoundingMode, expected: list[int]
) -> None:
    values = ["5/2", "-5/2", "12/5", "-12/5", "13/5", "-13/5", "7/2"]
    rounded: list[int] = []
    for value in values:
        rounded.append(round_fraction(Fraction(value), mode))
    assert rounded == expected


def test_sg_gst_at_the_half_cent_boundary_rounds_half_even() -> None:
    # SGD 0.50 at 9% GST is 4.5 cents exactly.
    divided = divide(Money(50, Currency.SGD), Fraction(9, 100), RoundingMode.HALF_EVEN)
    assert divided.result == Money(4, Currency.SGD)
    assert divided.remainder == Fraction(1, 2)
    # SGD 1.50 at 9% is 13.5 cents: half-even goes up to the even 14.
    divided = divide(Money(150, Currency.SGD), Fraction(9, 100), RoundingMode.HALF_EVEN)
    assert divided.result == Money(14, Currency.SGD)
    assert divided.remainder == Fraction(-1, 2)


def test_id_vat_on_a_zero_decimal_amount() -> None:
    # IDR 123,457 at 11% is 13,580.27 rupiah; IDR has no minor unit.
    divided = divide(
        Money(123_457, Currency.IDR), Fraction(11, 100), RoundingMode.HALF_EVEN
    )
    assert divided.result == Money(13_580, Currency.IDR)
    assert divided.remainder == Fraction(27, 100)


def test_refund_amount_rounds_symmetrically_with_half_away_from_zero() -> None:
    tax = divide(
        Money(150, Currency.SGD), Fraction(9, 100), RoundingMode.HALF_AWAY_FROM_ZERO
    )
    refund_tax = divide(
        Money(-150, Currency.SGD), Fraction(9, 100), RoundingMode.HALF_AWAY_FROM_ZERO
    )
    assert refund_tax.result.amount_minor == -tax.result.amount_minor


def test_allocate_vnd_one_million_three_ways() -> None:
    parts = allocate(Money(1_000_000, Currency.VND), [1, 1, 1])
    assert parts == [
        Money(333_334, Currency.VND),
        Money(333_333, Currency.VND),
        Money(333_333, Currency.VND),
    ]


def test_allocate_a_refund_three_ways_keeps_the_sign() -> None:
    parts = allocate(Money(-100, Currency.SGD), [1, 1, 1])
    assert parts == [
        Money(-34, Currency.SGD),
        Money(-33, Currency.SGD),
        Money(-33, Currency.SGD),
    ]


def test_allocate_gives_leftover_to_the_largest_remainder() -> None:
    # 11 by 1:3 is 2.75 and 8.25: the one leftover unit goes to the 0.75.
    assert allocate(Money(11, Currency.THB), [1, 3]) == [
        Money(3, Currency.THB),
        Money(8, Currency.THB),
    ]


def test_allocate_with_a_zero_weight_gives_it_nothing() -> None:
    assert allocate(Money(7, Currency.PHP), [0, 1, 1]) == [
        Money(0, Currency.PHP),
        Money(4, Currency.PHP),
        Money(3, Currency.PHP),
    ]


def test_allocate_refuses_weights_that_cannot_split() -> None:
    for weights in [[], [0, 0], [1, -1]]:
        with pytest.raises(ValueError):
            allocate(Money(100, Currency.SGD), weights)


amounts = st.integers(min_value=-(10**15), max_value=10**15)
ratios = st.fractions(min_value=-10, max_value=10, max_denominator=10**6)


@given(amount_minor=amounts, ratio=ratios, mode=st.sampled_from(RoundingMode))
def test_divide_result_plus_remainder_is_exact(
    amount_minor: int, ratio: Fraction, mode: RoundingMode
) -> None:
    divided = divide(Money(amount_minor, Currency.SGD), ratio, mode)
    assert divided.result.amount_minor + divided.remainder == amount_minor * ratio
    assert abs(divided.remainder) < 1


@given(amount_minor=amounts, ratio=ratios)
def test_half_modes_leave_at_most_half_a_unit(
    amount_minor: int, ratio: Fraction
) -> None:
    for mode in [RoundingMode.HALF_EVEN, RoundingMode.HALF_AWAY_FROM_ZERO]:
        divided = divide(Money(amount_minor, Currency.SGD), ratio, mode)
        assert abs(divided.remainder) <= Fraction(1, 2)


@given(
    amount_minor=amounts,
    weights=st.lists(st.integers(min_value=0, max_value=10**6), min_size=1).filter(
        lambda ws: sum(ws) > 0
    ),
)
@example(amount_minor=1, weights=[1, 1])
def test_allocate_parts_sum_to_the_total_and_stay_near_their_share(
    amount_minor: int, weights: list[int]
) -> None:
    parts = allocate(Money(amount_minor, Currency.VND), weights)
    total = 0
    for part, weight in zip(parts, weights, strict=True):
        total += part.amount_minor
        exact_share = Fraction(amount_minor * weight, sum(weights))
        assert abs(part.amount_minor - exact_share) < 1
    assert total == amount_minor
