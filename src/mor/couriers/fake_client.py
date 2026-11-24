"""Client for the fake courier's rate call (mor.fakes.courier), shaped like
any real provider module: our records in, our result union out, never our
database.

How a call ends:
- connection refused or no such host: `Unavailable`, it never left;
- a timeout (bounded by the account's timeout), a dropped connection or a
  5xx: `OutcomeUnknown`;
- 404 `not_served`: `NotServed`;
- anything else it does not document, or parts that do not add up to the
  total: `UndecodableBody`.
"""

import asyncio

import httpx2
import msgspec

from mor.couriers.port import (
    NotServed,
    QuoteRateResult,
    carrier_price,
)
from mor.couriers.records import (
    CourierAccount,
    PricePart,
    PricePartKind,
    PriceQuestion,
)
from mor.dates import AwareDatetime, to_utc
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
    http: httpx2.AsyncClient, account: CourierAccount, question: PriceQuestion
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
