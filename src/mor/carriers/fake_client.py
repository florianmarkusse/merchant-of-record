"""Client for the fake carrier (mor.fakes.carrier): its rate call and its
labels, shaped like any real provider module: our records in, our result
union out, never our database.

How a call ends:
- connection refused or no such host: `Unavailable`, it never left;
- a timeout (bounded by the account's timeout), a dropped connection or a
  5xx: `OutcomeUnknown`;
- 404 `not_served` on a rate: `NotServed`; 422 on a booking: `BookRefused`;
- anything else it does not document, parts that do not add up to the
  total, or a document that is not a PDF: `UndecodableBody`.
"""

import asyncio
from typing import Literal

import httpx2
import msgspec

from mor.carriers.port import (
    BookRefusal,
    BookRefused,
    BookRequest,
    BookResult,
    Cancelled,
    CancelResult,
    FindResult,
    Found,
    FoundLabel,
    Label,
    LabelState,
    NotServed,
    Party,
    QuoteRateResult,
    carrier_price,
)
from mor.carriers.records import (
    CarrierAccount,
    PricePart,
    PricePartKind,
    PriceQuestion,
)
from mor.dates import AwareDatetime, to_utc
from mor.ids import ExportDeclarationNo, TrackingNumber
from mor.money.currency import Currency
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


class _Part(msgspec.Struct, frozen=True):
    kind: str
    name: str
    amount_minor: int


class _Answer(msgspec.Struct, frozen=True, kw_only=True):
    currency: str
    total_minor: int
    parts: list[_Part]
    valid_until: AwareDatetime | None


PDF_MAGIC = b"%PDF-"


class _Error(msgspec.Struct, frozen=True):
    error: str
    detail: str = ""


def _kind(text: str) -> PricePartKind | None:
    for kind in PricePartKind:
        if kind.value == text:
            return kind
    return None


def _currency(text: str) -> Currency | None:
    for currency in Currency:
        if currency.value == text:
            return currency
    return None


async def quote_rate(
    http: httpx2.AsyncClient, account: CarrierAccount, question: PriceQuestion
) -> QuoteRateResult:
    parcel = question.parcel
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            response = await http.post(
                account.base_url + "/rates",
                json={
                    "service": question.service,
                    "origin": question.origin,
                    "destination": question.destination,
                    "weight_g": parcel.weight_g,
                    "length_mm": parcel.length_mm,
                    "width_mm": parcel.width_mm,
                    "height_mm": parcel.height_mm,
                },
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if response.status_code >= 500:
        return OutcomeUnknown(f"status {response.status_code}")
    if response.status_code == 404:
        try:
            error = msgspec.json.decode(response.content, type=_Error)
        except msgspec.DecodeError:
            return UndecodableBody("404 without an error body")
        if error.error == "not_served":
            return NotServed(error.detail or "not served")
        return UndecodableBody(f"404 {error.error}")
    if response.status_code != 200:
        return UndecodableBody(f"status {response.status_code}")
    try:
        answer = msgspec.json.decode(response.content, type=_Answer)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    currency = _currency(answer.currency)
    if currency is None:
        return UndecodableBody(f"unknown currency {answer.currency!r}")
    parts: list[PricePart] = []
    for part in answer.parts:
        kind = _kind(part.kind)
        if kind is None:
            return UndecodableBody(f"unknown part kind {part.kind!r}")
        parts.append(PricePart(kind, part.name, part.amount_minor))
    valid_until = None if answer.valid_until is None else to_utc(answer.valid_until)
    return carrier_price(currency, answer.total_minor, tuple(parts), valid_until)


# --- booking ----------------------------------------------------------------


class _LabelAnswer(msgspec.Struct, frozen=True, kw_only=True):
    tracking_number: str
    export_declaration_no: str
    label_pdf: bytes
    commercial_invoice_pdf: bytes


class _FoundAnswer(msgspec.Struct, frozen=True, kw_only=True):
    tracking_number: str
    export_declaration_no: str
    state: Literal["live", "cancelled"]
    label_pdf: bytes
    commercial_invoice_pdf: bytes


class _FindAnswer(msgspec.Struct, frozen=True):
    shipments: list[_FoundAnswer]


def _party(party: Party) -> dict[str, str | None]:
    return {
        "name": party.name,
        "email": party.email,
        "phone": party.phone,
        "line1": party.line1,
        "line2": party.line2,
        "city": party.city,
        "region": party.region,
        "postcode": party.postcode,
        "country": party.country,
    }


def _book_body(request: BookRequest) -> dict[str, object]:
    items: list[dict[str, object]] = []
    for item in request.items:
        items.append(
            {
                "description": item.description,
                "hs_code": item.hs_code,
                "origin": item.origin,
                "quantity": item.quantity,
                "value_minor": item.value.amount_minor,
                "currency": item.value.currency.value,
            }
        )
    parcel = request.parcel
    return {
        "reference": request.reference,
        "service": request.service,
        "shipper": _party(request.shipper),
        "receiver": _party(request.receiver),
        "parcel": {
            "weight_g": parcel.weight_g,
            "length_mm": parcel.length_mm,
            "width_mm": parcel.width_mm,
            "height_mm": parcel.height_mm,
        },
        "incoterm": request.incoterm.value,
        "items": items,
    }


def _label(
    tracking_number: str,
    export_declaration_no: str,
    label_pdf: bytes,
    commercial_invoice_pdf: bytes,
) -> Label | UndecodableBody:
    if not tracking_number or not export_declaration_no:
        return UndecodableBody("a label without its numbers")
    if not label_pdf.startswith(PDF_MAGIC) or not commercial_invoice_pdf.startswith(
        PDF_MAGIC
    ):
        return UndecodableBody("a document that is not a PDF")
    return Label(
        tracking_number=TrackingNumber(tracking_number),
        export_declaration_no=ExportDeclarationNo(export_declaration_no),
        label_pdf=label_pdf,
        commercial_invoice_pdf=commercial_invoice_pdf,
    )


async def book(
    http: httpx2.AsyncClient, account: CarrierAccount, request: BookRequest
) -> BookResult:
    """A 422 is a refusal by the carrier: nothing was booked."""
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            response = await http.post(
                account.base_url + "/shipments",
                json=_book_body(request),
                headers={"Idempotency-Key": request.key},
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if response.status_code >= 500:
        return OutcomeUnknown(f"status {response.status_code}")
    if response.status_code == 422:
        try:
            error = msgspec.json.decode(response.content, type=_Error)
        except msgspec.DecodeError:
            return UndecodableBody("422 without an error body")
        match error.error:
            case "address_refused":
                return BookRefused(BookRefusal.ADDRESS, error.detail)
            case "customs_refused":
                return BookRefused(BookRefusal.CUSTOMS, error.detail)
            case _:
                return BookRefused(BookRefusal.OTHER, f"{error.error}: {error.detail}")
    if response.status_code != 201:
        return UndecodableBody(f"status {response.status_code}")
    try:
        answer = msgspec.json.decode(response.content, type=_LabelAnswer)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return _label(
        answer.tracking_number,
        answer.export_declaration_no,
        answer.label_pdf,
        answer.commercial_invoice_pdf,
    )


async def find_by_reference(
    http: httpx2.AsyncClient, account: CarrierAccount, reference: str
) -> FindResult:
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            response = await http.get(
                account.base_url + "/shipments", params={"reference": reference}
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if response.status_code >= 500:
        return OutcomeUnknown(f"status {response.status_code}")
    if response.status_code != 200:
        return UndecodableBody(f"status {response.status_code}")
    try:
        answer = msgspec.json.decode(response.content, type=_FindAnswer)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    found: list[FoundLabel] = []
    for shipment in answer.shipments:
        label = _label(
            shipment.tracking_number,
            shipment.export_declaration_no,
            shipment.label_pdf,
            shipment.commercial_invoice_pdf,
        )
        if isinstance(label, UndecodableBody):
            return label
        match shipment.state:
            case "live":
                state = LabelState.LIVE
            case "cancelled":
                state = LabelState.CANCELLED
        found.append(FoundLabel(label, state))
    return Found(tuple(found))


async def cancel(
    http: httpx2.AsyncClient, account: CarrierAccount, tracking_number: TrackingNumber
) -> CancelResult:
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            response = await http.post(
                f"{account.base_url}/shipments/{tracking_number}/cancel"
            )
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if response.status_code >= 500:
        return OutcomeUnknown(f"status {response.status_code}")
    if response.status_code != 200:
        return UndecodableBody(f"status {response.status_code}")
    return Cancelled()
