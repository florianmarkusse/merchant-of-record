import pytest
from hypothesis import given, strategies

from mor.money.amount import (
    MAX_MINOR,
    MIN_MINOR,
    CurrencyMismatch,
    InvalidAmount,
    Money,
    add,
    format_major,
    parse_major,
    subtract,
)
from mor.money.currency import Currency


def test_money_refuses_a_float_amount() -> None:
    with pytest.raises(TypeError):
        Money(12.5, Currency.SGD)  # type: ignore[arg-type]


def test_money_refuses_a_bool_amount() -> None:
    with pytest.raises(TypeError):
        Money(True, Currency.SGD)


def test_money_refuses_an_amount_beyond_bigint() -> None:
    with pytest.raises(ValueError):
        Money(MAX_MINOR + 1, Currency.VND)


def test_adding_two_currencies_is_a_bug() -> None:
    with pytest.raises(CurrencyMismatch):
        add(Money(100, Currency.SGD), Money(100, Currency.MYR))
    with pytest.raises(CurrencyMismatch):
        subtract(Money(100, Currency.SGD), Money(100, Currency.MYR))


def test_parse_two_decimal_currency() -> None:
    assert parse_major("12.34", Currency.SGD) == Money(1234, Currency.SGD)
    assert parse_major("12.3", Currency.SGD) == Money(1230, Currency.SGD)
    assert parse_major("12", Currency.SGD) == Money(1200, Currency.SGD)
    assert parse_major("-0.05", Currency.THB) == Money(-5, Currency.THB)


def test_parse_zero_decimal_currencies() -> None:
    assert parse_major("1000000", Currency.VND) == Money(1_000_000, Currency.VND)
    # IDR is exponent 0 by our own decision, not ISO 4217's 2.
    assert parse_major("150000", Currency.IDR) == Money(150_000, Currency.IDR)
    assert parse_major("150000.00", Currency.IDR) == InvalidAmount(
        "150000.00", "too_many_decimals"
    )


def test_parse_refuses_what_is_not_a_plain_decimal() -> None:
    for text in ["", "1,000", "1e3", "12.", ".5", "+1", " 1", "NaN", "１２"]:
        assert parse_major(text, Currency.SGD) == InvalidAmount(text, "not_a_number")


def test_parse_refuses_too_many_decimals() -> None:
    assert parse_major("12.345", Currency.SGD) == InvalidAmount(
        "12.345", "too_many_decimals"
    )


def test_parse_refuses_an_amount_beyond_bigint() -> None:
    text = str(MAX_MINOR + 1)
    assert parse_major(text, Currency.VND) == InvalidAmount(text, "out_of_range")


def test_format_major() -> None:
    assert format_major(Money(1234, Currency.SGD)) == "12.34"
    assert format_major(Money(5, Currency.SGD)) == "0.05"
    assert format_major(Money(-5, Currency.SGD)) == "-0.05"
    assert format_major(Money(0, Currency.MYR)) == "0.00"
    assert format_major(Money(-1_000_000, Currency.VND)) == "-1000000"


@given(
    amount_minor=strategies.integers(min_value=MIN_MINOR, max_value=MAX_MINOR),
    currency=strategies.sampled_from(Currency),
)
def test_format_then_parse_round_trips(amount_minor: int, currency: Currency) -> None:
    money = Money(amount_minor, currency)
    assert parse_major(format_major(money), currency) == money


@given(text=strategies.text(max_size=30), currency=strategies.sampled_from(Currency))
def test_parse_any_text_gives_money_or_invalid_amount(
    text: str, currency: Currency
) -> None:
    result = parse_major(text, currency)
    assert isinstance(result, Money | InvalidAmount)
