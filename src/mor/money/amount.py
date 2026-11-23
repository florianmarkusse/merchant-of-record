import re
from dataclasses import dataclass
from typing import Literal

from mor.money.currency import Currency, exponent


# The range of a PostgreSQL bigint, where amounts are stored.
MIN_MINOR = -(2**63)
MAX_MINOR = 2**63 - 1


class CurrencyMismatch(Exception):
    """Two amounts in different currencies were combined: a bug in the
    caller, never an expected failure."""


@dataclass(frozen=True)
class Money:
    """An amount in its currency's minor unit (cents, or whole dong)."""

    amount_minor: int
    currency: Currency

    def __post_init__(self) -> None:
        # Python does not check types at runtime; a float or bool here is a
        # bug, and so is an amount the database cannot store.
        if type(self.amount_minor) is not int:
            raise TypeError(f"amount_minor must be int, got {self.amount_minor!r}")
        if not MIN_MINOR <= self.amount_minor <= MAX_MINOR:
            raise ValueError(f"amount_minor out of bigint range: {self.amount_minor}")


@dataclass(frozen=True)
class InvalidAmount:
    text: str
    reason: Literal["not_a_number", "too_many_decimals", "out_of_range"]


def add(a: Money, b: Money) -> Money:
    if a.currency != b.currency:
        raise CurrencyMismatch(f"{a.currency} + {b.currency}")
    return Money(a.amount_minor + b.amount_minor, a.currency)


def subtract(a: Money, b: Money) -> Money:
    if a.currency != b.currency:
        raise CurrencyMismatch(f"{a.currency} - {b.currency}")
    return Money(a.amount_minor - b.amount_minor, a.currency)


_MAJOR = re.compile(r"(-?)([0-9]+)(?:\.([0-9]+))?")


def parse_major(text: str, currency: Currency) -> Money | InvalidAmount:
    """Parse a decimal string in major units ("12.34") at an edge. Digits are
    read by hand, never through float. More decimals than the currency has is
    refused: a caller that must round does so explicitly with `divide`."""
    match = _MAJOR.fullmatch(text)
    if match is None:
        return InvalidAmount(text, "not_a_number")
    sign, whole, fraction = match.group(1), match.group(2), match.group(3) or ""
    digits = exponent(currency)
    if len(fraction) > digits:
        return InvalidAmount(text, "too_many_decimals")
    minor = int(whole + fraction.ljust(digits, "0"))
    if sign == "-":
        minor = -minor
    if not MIN_MINOR <= minor <= MAX_MINOR:
        return InvalidAmount(text, "out_of_range")
    return Money(minor, currency)


def format_major(money: Money) -> str:
    """The amount in major units as a plain decimal string ("-12.30"), for
    payloads and logs. Display formatting with grouping is a page's job."""
    digits = exponent(money.currency)
    sign = "-" if money.amount_minor < 0 else ""
    magnitude = str(abs(money.amount_minor))
    if digits == 0:
        return sign + magnitude
    magnitude = magnitude.rjust(digits + 1, "0")
    return f"{sign}{magnitude[:-digits]}.{magnitude[-digits:]}"
