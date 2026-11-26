"""The carrier port: `quote_rate`, a live price (docs/plan-quote.md §4,
4c-2), and booking a label: `book`, `find_by_reference` and `cancel`
(docs/plan-shipments.md §6). Tracking comes in 6b
(plan-architecture.md §7 lists every operation). Our records and result
unions, no carrier words.

A carrier's rate call (DHL Express `/mydhlapi/rates`, FedEx
`rate/v1/rates/quotes`) takes the service, origin, destination and a
parcel's weight and box, and returns a total with its parts. We never send
the goods value: duty and tax are ours to price.

A booking sends our shipment id as the shipper reference, so
`find_by_reference` lists every label made for it, and our request's key
as the idempotency key where the carrier keeps one (`book_guard`).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from mor.carriers.records import Parcel, PricePart
from mor.ids import (
    CarrierIdempotencyKey,
    CountryCode,
    ExportDeclarationNo,
    HsCode,
    ServiceCode,
    TrackingNumber,
)
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.vendor_failures import CallFailure, UndecodableBody


@dataclass(frozen=True, kw_only=True)
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
    return CarrierPrice(
        currency=currency, total_minor=total_minor, parts=parts, valid_until=valid_until
    )


# --- booking ----------------------------------------------------------------


class Incoterm(StrEnum):
    """Who pays what is due at the border; matches `incoterm`. `ddp`: the
    carrier pays it and bills us; `dap`: the buyer pays it at the door."""

    DDP = "ddp"
    DAP = "dap"


@dataclass(frozen=True, kw_only=True)
class Party:
    """The shipper or the receiver, as the label prints them. Personal
    data: never logged."""

    name: str
    email: str
    phone: str  # dial code and number: `+6591234567`
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: CountryCode


@dataclass(frozen=True, kw_only=True)
class CustomsItem:
    description: str
    hs_code: HsCode  # ours, never the merchant's
    origin: CountryCode
    quantity: int
    value: Money  # the line's whole value


@dataclass(frozen=True, kw_only=True)
class BookRequest:
    reference: str  # our shipment id, the shipper reference
    key: CarrierIdempotencyKey
    service: ServiceCode
    shipper: Party
    receiver: Party
    parcel: Parcel
    incoterm: Incoterm
    items: tuple[CustomsItem, ...]


@dataclass(frozen=True, kw_only=True)
class Label:
    """A label the carrier made, with the documents to hand over: both
    PDFs."""

    tracking_number: TrackingNumber
    export_declaration_no: ExportDeclarationNo
    label_pdf: bytes
    commercial_invoice_pdf: bytes


class BookRefusal(StrEnum):
    """Why a carrier refused a booking, as far as we tell them apart."""

    ADDRESS = "address"
    CUSTOMS = "customs"
    OTHER = "other"


@dataclass(frozen=True)
class BookRefused:
    """A documented refusal: nothing was booked."""

    refusal: BookRefusal
    detail: str


type BookResult = Label | BookRefused | CallFailure


class LabelState(StrEnum):
    LIVE = "live"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class FoundLabel:
    label: Label
    state: LabelState


@dataclass(frozen=True)
class Found:
    """Every label the carrier holds for a reference, oldest first."""

    labels: tuple[FoundLabel, ...]


type FindResult = Found | CallFailure


@dataclass(frozen=True)
class Cancelled:
    """The label is cancelled, now or before: cancelling twice is safe."""


type CancelResult = Cancelled | CallFailure
