"""The duty vendor port: our records and result unions, no vendor words
(docs/plan-quote.md §3, 4b-2).

A vendor is asked for one item's rates: (destination, origin, HS code) in,
per charge the formula as the vendor wrote it, our exact rate when the
formula is a percentage, and the vendor's note. A landed-cost vendor
(Zonos) is asked with a one-item cart; its `duties[]` become the duty and
its `taxes[]` the import tax.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from fractions import Fraction

from mor.markets.records import Charge


class DutyProvider(StrEnum):
    """The module that speaks to a vendor; matches `duty_provider`. A
    vendor's name (`zonos`) is data; its provider is code."""

    FAKE = "fake"


@dataclass(frozen=True)
class DutyVendorAccount:
    id: uuid.UUID
    vendor_name: str
    provider: DutyProvider
    base_url: str
    timeout: timedelta


@dataclass(frozen=True)
class RateQuestion:
    destination: str
    origin: str
    hs_code: str  # digits only


@dataclass(frozen=True)
class VendorRate:
    charge: Charge
    formula: str  # as the vendor wrote it: "30%", "2.00 USD/kg"
    rate: Fraction | None  # None: not a plain percentage
    note: str | None  # e.g. a preference the vendor applied on its own


@dataclass(frozen=True)
class VendorRates:
    hs_code_used: str  # the vendor may correct the code it was given
    rates: tuple[VendorRate, ...]


@dataclass(frozen=True)
class UnknownCode:
    """The vendor does not know the HS code: a documented refusal."""


@dataclass(frozen=True)
class Unavailable:
    """The request provably never reached the vendor (connection refused)."""

    detail: str


@dataclass(frozen=True)
class OutcomeUnknown:
    """No answer (timeout, dropped connection, a 5xx). A lookup is a read,
    so asking again is safe; it costs one more call."""

    detail: str


@dataclass(frozen=True)
class UndecodableBody:
    """An answer that is not what the vendor's API documents."""

    detail: str


type AskResult = (
    VendorRates | UnknownCode | Unavailable | OutcomeUnknown | UndecodableBody
)


_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")


def percent_formula(formula: str) -> Fraction | None:
    """A formula that is a plain percentage ("12.5%"), as an exact rate
    (Fraction(1, 8)); anything else (a specific duty, a compound) None."""
    match = _PERCENT.fullmatch(formula.strip())
    if match is None:
        return None
    return Fraction(match.group(1)) / 100
