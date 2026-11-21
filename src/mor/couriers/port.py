"""The courier port, with the one operation 4c-2 needs: `quote_rate`, a
live price (docs/plan-quote.md §4, 4c-2; plan-architecture.md §7, which
lists the operations slice 6 adds). Our records and result unions, no
carrier words.

A carrier's rate call (DHL Express `/mydhlapi/rates`, FedEx
`rate/v1/rates/quotes`) takes the service, origin, destination and a
parcel's weight and box, and returns a total with its parts. We never send
the goods value: duty and tax are ours to price.
"""

from dataclasses import dataclass
from datetime import datetime

from mor.couriers.records import PricePart
from mor.money.currency import Currency
from mor.vendor_failures import CallFailure, UndecodableBody


@dataclass(frozen=True)
class CarrierPrice:
    currency: Currency
    total_minor: int
    parts: tuple[PricePart, ...]  # they sum to the total (`carrier_price`)
    valid_until: datetime | None  # when the carrier says so


@dataclass(frozen=True)
class NotServed:
    """The carrier does not carry this service, lane or parcel: a
    documented refusal."""

    detail: str


type QuoteRateResult = CarrierPrice | NotServed | CallFailure


def carrier_price(
    currency: Currency,
    total_minor: int,
    parts: tuple[PricePart, ...],
    valid_until: datetime | None,
) -> CarrierPrice | UndecodableBody:
    """A decoded answer, refused unless its parts sum to its total and none
    is negative: every provider module ends its decoding here."""
    summed = 0
    for part in parts:
        if part.amount_minor < 0:
            return UndecodableBody(f"part {part.name} is negative")
        summed += part.amount_minor
    if summed != total_minor:
        return UndecodableBody(f"parts sum to {summed}, the total is {total_minor}")
    return CarrierPrice(currency, total_minor, parts, valid_until)
