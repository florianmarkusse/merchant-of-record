from enum import StrEnum
from typing import assert_never


class Currency(StrEnum):
    """ISO 4217 codes we support. Adding one is a member here, its case in
    `exponent`, and a value in the database's `currency` enum type."""

    VND = "VND"
    IDR = "IDR"
    SGD = "SGD"
    MYR = "MYR"
    THB = "THB"
    PHP = "PHP"
    USD = "USD"
    EUR = "EUR"
    # Limits and FX rates of the destinations (docs/plan-quote.md §2).
    GBP = "GBP"
    AUD = "AUD"
    NZD = "NZD"
    CAD = "CAD"
    CHF = "CHF"
    JPY = "JPY"
    KRW = "KRW"


def exponent(currency: Currency) -> int:
    """The one table of minor-unit exponents: an amount in minor units is the
    major amount times 10 ** exponent. Exhaustive, so pyright refuses a
    currency without one."""
    match currency:
        case Currency.VND:
            return 0
        case Currency.IDR:
            # ISO 4217 says 2. We use 0 (decided 04-Nov-2026, AGENTS.md):
            # sub-rupiah amounts are not used in practice and Adyen uses 0.
            # Provider modules convert to and from their own exponent.
            return 0
        case Currency.SGD | Currency.MYR | Currency.THB | Currency.PHP:
            return 2
        case Currency.USD | Currency.EUR:
            return 2
        case Currency.GBP | Currency.AUD | Currency.NZD | Currency.CAD | Currency.CHF:
            return 2
        case Currency.JPY | Currency.KRW:
            return 0
        case _:
            assert_never(currency)
