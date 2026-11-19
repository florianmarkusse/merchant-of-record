"""The merchant API's wire format (plan-merchant-surfaces §4;
docs/plan-quote.md §5, 4d-1): what is decoded at the edge, what is sent
back, and the one error shape.

Every body is decoded by msgspec into a frozen record with its limits
(`Meta`) before anything else sees it. Unknown fields are refused, so a
field v1 does not take (`tax_behavior`) is an error, not ignored.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

import msgspec
from msgspec import Meta
from starlette.responses import Response

from mor.checkout.records import CheckoutSession, Customs, SessionDraft, SoldLine
from mor.couriers.records import Parcel
from mor.dates import format_instant
from mor.markets.records import MarketSet
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.public_ids import MERCHANT_PREFIX, SESSION_PREFIX, public_id


# --- what comes in -----------------------------------------------------------------

type Text = Annotated[str, Meta(min_length=1, max_length=500)]
type Country = Annotated[str, Meta(pattern="^[A-Z]{2}$")]
type Url = Annotated[str, Meta(pattern="^https?://", max_length=2000)]


class MoneyIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    amount_minor: Annotated[int, Meta(ge=0, le=10**15)]
    currency: Annotated[str, Meta(pattern="^[A-Z]{3}$")]


class CustomsIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    description: Text
    origin_country: Country
    weight_g: Annotated[int, Meta(ge=1, le=1_000_000)]  # one unit's
    hs_code: Annotated[str, Meta(pattern="^[0-9]{4,10}$")] | None = None


class LineIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    name: Text
    quantity: Annotated[int, Meta(ge=1, le=10_000)]
    unit_price: MoneyIn
    category: Annotated[str, Meta(min_length=1, max_length=100)]
    customs: CustomsIn
    merchant_sku: Annotated[str, Meta(min_length=1, max_length=200)] | None = None


class ParcelIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    weight_g: Annotated[int, Meta(ge=1, le=1_000_000)]
    length_mm: Annotated[int, Meta(ge=1, le=10_000)]
    width_mm: Annotated[int, Meta(ge=1, le=10_000)]
    height_mm: Annotated[int, Meta(ge=1, le=10_000)]


type Lines = Annotated[list[LineIn], Meta(min_length=1, max_length=100)]


class SessionIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    lines: Lines
    parcel: ParcelIn
    return_url: Url
    cancel_url: Url
    merchant_reference: Annotated[str, Meta(min_length=1, max_length=200)] | None = None


class EstimateIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    lines: Lines
    parcel: ParcelIn
    country: Country


# --- errors ------------------------------------------------------------------------


class ErrorType(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    IDEMPOTENCY = "idempotency"
    NOT_FOUND = "not_found"
    QUOTE_REFUSED = "quote_refused"
    PENDING = "pending"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"  # on our side: no market data, no pricing


type ErrorCode = Literal[
    "invalid_body",
    "invalid_field",
    "idempotency_key_missing",
    "currency_not_supported",
    "unknown_category",
    "authentication_failed",
    "idempotency_key_reused",
    "not_found",
    "country_not_supported",
    "country_closed",
    "classification_needed",
    "duty_rate_unavailable",
    "no_service_for_lane",
    "estimate_pending",
    "rate_limited",
    "no_market_data",
    "pricing_unavailable",
]


@dataclass(frozen=True)
class ApiError:
    status: int
    type: ErrorType
    code: ErrorCode
    message: str
    param: str | None = None
    retry_after_s: int | None = None


class _ErrorBody(msgspec.Struct, frozen=True):
    type: str
    code: str
    message: str
    param: str | None


class _ErrorOut(msgspec.Struct, frozen=True):
    error: _ErrorBody


def error_response(error: ApiError) -> Response:
    body = _ErrorOut(
        _ErrorBody(error.type.value, error.code, error.message, error.param)
    )
    headers: dict[str, str] = {}
    if error.retry_after_s is not None:
        headers["Retry-After"] = str(error.retry_after_s)
    return Response(
        msgspec.json.encode(body),
        status_code=error.status,
        media_type="application/json",
        headers=headers,
    )


def json_response(status: int, body: bytes) -> Response:
    return Response(body, status_code=status, media_type="application/json")


# msgspec names the field in its message: "… - at `$.lines[0].quantity`".
_AT = re.compile(r" - at `\$\.?(.*)`$")


def decode[T](body: bytes, record: type[T]) -> T | ApiError:
    """A body into its record, or `invalid_request` naming the field."""
    try:
        return msgspec.json.decode(body, type=record)
    except msgspec.ValidationError as error:
        message = str(error)
        param: str | None = None
        found = _AT.search(message)
        if found is not None:
            param = found.group(1) or None
            message = message[: found.start()]
        return ApiError(400, ErrorType.INVALID_REQUEST, "invalid_field", message, param)
    except msgspec.DecodeError as error:
        return ApiError(400, ErrorType.INVALID_REQUEST, "invalid_body", str(error))


def request_sha256(endpoint: str, record: msgspec.Struct) -> bytes:
    """What an idempotency key is checked against: the decoded request
    re-encoded (fields in their declared order), so whitespace and key
    order in the merchant's JSON do not matter."""
    return hashlib.sha256(
        endpoint.encode() + b"\n" + msgspec.json.encode(record)
    ).digest()


# --- into our records --------------------------------------------------------------


def _currency(code: str, param: str) -> Currency | ApiError:
    for currency in Currency:
        if currency.value == code:
            return currency
    return ApiError(
        400,
        ErrorType.INVALID_REQUEST,
        "currency_not_supported",
        f"{code} is not a currency we take",
        param,
    )


def sold_lines(
    market: MarketSet, lines: list[LineIn]
) -> tuple[SoldLine, ...] | ApiError:
    """The lines as our records, each category one of ours (answer 2 of
    19-Nov-2026: categories are a closed list; classification is ours)."""
    categories: set[str] = set()
    for category in market.categories:
        categories.add(category.category)
    sold: list[SoldLine] = []
    for number, line in enumerate(lines):
        currency = _currency(line.unit_price.currency, f"lines[{number}].unit_price")
        if isinstance(currency, ApiError):
            return currency
        if line.category not in categories:
            return ApiError(
                400,
                ErrorType.INVALID_REQUEST,
                "unknown_category",
                f"{line.category!r} is not one of our categories",
                f"lines[{number}].category",
            )
        customs = line.customs
        sold.append(
            SoldLine(
                line.name,
                line.quantity,
                Money(line.unit_price.amount_minor, currency),
                line.category,
                line.merchant_sku,
                Customs(
                    customs.description,
                    customs.origin_country,
                    customs.weight_g,
                    customs.hs_code,
                ),
            )
        )
    return tuple(sold)


def parcel_of(parcel: ParcelIn) -> Parcel:
    return Parcel(parcel.weight_g, parcel.length_mm, parcel.width_mm, parcel.height_mm)


def session_draft(market: MarketSet, request: SessionIn) -> SessionDraft | ApiError:
    lines = sold_lines(market, request.lines)
    if isinstance(lines, ApiError):
        return lines
    return SessionDraft(
        lines,
        parcel_of(request.parcel),
        request.merchant_reference,
        request.return_url,
        request.cancel_url,
    )


# --- what goes out -----------------------------------------------------------------


class MoneyOut(msgspec.Struct, frozen=True):
    amount_minor: int
    currency: str


def money_out(money: Money) -> MoneyOut:
    return MoneyOut(money.amount_minor, money.currency.value)


class CustomsOut(msgspec.Struct, frozen=True):
    description: str
    origin_country: str
    weight_g: int
    hs_code: str | None


class LineOut(msgspec.Struct, frozen=True):
    name: str
    quantity: int
    unit_price: MoneyOut
    category: str
    merchant_sku: str | None
    customs: CustomsOut


class ParcelOut(msgspec.Struct, frozen=True):
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


class SessionOut(msgspec.Struct, frozen=True):
    id: str
    object: str
    merchant: str
    state: str
    url: str
    created_at: str
    expires_at: str
    merchant_reference: str | None
    return_url: str
    cancel_url: str
    parcel: ParcelOut
    lines: list[LineOut]


def session_out(session: CheckoutSession, self_url: str) -> bytes:
    """A session as the API shows it; times through `format_instant`."""
    session_id = public_id(SESSION_PREFIX, session.id)
    lines: list[LineOut] = []
    for line in session.lines:
        customs = line.customs
        lines.append(
            LineOut(
                line.name,
                line.quantity,
                money_out(line.unit_price),
                line.category,
                line.merchant_sku,
                CustomsOut(
                    customs.description,
                    customs.origin_country,
                    customs.weight_g,
                    customs.hs_code,
                ),
            )
        )
    parcel = session.parcel
    out = SessionOut(
        session_id,
        "checkout_session",
        public_id(MERCHANT_PREFIX, session.merchant_id),
        session.state.value,
        f"{self_url}/c/{session_id}",
        format_instant(session.created_at),
        format_instant(session.expires_at),
        session.merchant_reference,
        session.return_url,
        session.cancel_url,
        ParcelOut(parcel.weight_g, parcel.length_mm, parcel.width_mm, parcel.height_mm),
        lines,
    )
    return msgspec.json.encode(out)
