"""The merchant API's wire format (plan-merchant-surfaces §4;
docs/plan-quote.md §5, 4d-1): what is decoded at the edge, what is sent
back, and the one error shape.

Every body is decoded by msgspec into a frozen record with its limits
(`Meta`) before anything else sees it. Unknown fields are refused, so a
field v1 does not take (`tax_behavior`) is an error, not ignored.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

import msgspec
from msgspec import Meta
from starlette.responses import Response

from mor.checkout.addresses import Address
from mor.checkout.quotes import MadeQuote, RefusedQuote, StoredQuote
from mor.checkout.records import (
    AfterPayment,
    CheckoutSession,
    Customs,
    SessionDraft,
    SoldLine,
)
from mor.couriers.records import Parcel
from mor.dates import format_instant
from mor.markets.records import MarketSet
from mor.merchants.records import Merchant
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.orders.store import Order
from mor.public_ids import (
    MERCHANT_PREFIX,
    ORDER_PREFIX,
    QUOTE_PREFIX,
    SESSION_PREFIX,
    public_id,
)


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
    # Where the buyer lands once paid (docs/plan-payments.md §6.1).
    after_payment: Literal["our_page", "merchant_page"] = "our_page"


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
        AfterPayment(request.after_payment),
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


class QuoteOptionOut(msgspec.Struct, frozen=True):
    key: str
    service: str
    name: str
    total: MoneyOut


class RefusalOut(msgspec.Struct, frozen=True):
    code: str
    message: str


class QuoteOut(msgspec.Struct, frozen=True):
    """The buyer's quote, without its build-up (the estimate gives that)."""

    id: str
    state: str
    currency: str | None
    valid_until: str | None
    options: list[QuoteOptionOut]
    refusal: RefusalOut | None


def quote_out(stored: StoredQuote) -> QuoteOut:
    quote_id = public_id(QUOTE_PREFIX, stored.id)
    match stored:
        case MadeQuote():
            options: list[QuoteOptionOut] = []
            for option in stored.options:
                options.append(
                    QuoteOptionOut(
                        option.key,
                        option.service,
                        option.service_name,
                        money_out(Money(option.total_minor, stored.currency)),
                    )
                )
            return QuoteOut(
                quote_id,
                "made",
                stored.currency.value,
                format_instant(stored.valid_until),
                options,
                None,
            )
        case RefusedQuote():
            return QuoteOut(
                quote_id,
                "refused",
                None,
                None,
                [],
                RefusalOut(stored.code, stored.message),
            )


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
    after_payment: str
    parcel: ParcelOut
    lines: list[LineOut]
    ship_to_country: str | None  # the full address comes with orders
    quote: QuoteOut | None
    order_id: str | None  # once paid


def session_out(
    session: CheckoutSession,
    self_url: str,
    ship_to_country: str | None,
    quote: StoredQuote | None,
    order_id: uuid.UUID | None,
) -> bytes:
    """A session as the API shows it; times through `format_instant`."""
    session_id = public_id(SESSION_PREFIX, session.id)
    lines = _lines_out(session.lines)
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
        session.after_payment.value,
        ParcelOut(parcel.weight_g, parcel.length_mm, parcel.width_mm, parcel.height_mm),
        lines,
        ship_to_country,
        None if quote is None else quote_out(quote),
        None if order_id is None else public_id(ORDER_PREFIX, order_id),
    )
    return msgspec.json.encode(out)


def _lines_out(sold: tuple[SoldLine, ...]) -> list[LineOut]:
    lines: list[LineOut] = []
    for line in sold:
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
    return lines


# --- orders and the merchant (docs/plan-payments.md §6.1) -------------------------


class ShippingOut(msgspec.Struct, frozen=True):
    key: str
    service: str
    name: str
    transit_days_min: int | None
    transit_days_max: int | None


class AddressOut(msgspec.Struct, frozen=True):
    """The full address, for fulfilment: personal data."""

    name: str
    email: str
    phone: str  # the dial code and the digits: `+6591234567`
    line1: str
    line2: str | None
    city: str
    region: str | None
    postcode: str | None
    country: str


class OrderOut(msgspec.Struct, frozen=True):
    id: str
    object: str
    merchant: str
    checkout_session: str
    merchant_reference: str | None
    state: str
    paid_at: str
    total: MoneyOut
    lines: list[LineOut]  # as the merchant sent them
    shipping: ShippingOut
    ship_to: AddressOut
    invoice_number: str | None  # once issued, moments after payment
    order_url: str  # the buyer's order page, with the invoice


def order_out(
    order: Order,
    session: CheckoutSession,
    quote: MadeQuote,
    address: Address,
    invoice_number: str | None,
    self_url: str,
) -> bytes:
    option = quote.options[order.option_no]
    low: int | None = None
    high: int | None = None
    if option.transit is not None:
        low, high = option.transit
    out = OrderOut(
        public_id(ORDER_PREFIX, order.id),
        "order",
        public_id(MERCHANT_PREFIX, order.merchant_id),
        public_id(SESSION_PREFIX, order.session_id),
        session.merchant_reference,
        "paid",
        format_instant(order.paid_at),
        money_out(Money(option.total_minor, quote.currency)),
        _lines_out(session.lines),
        ShippingOut(option.key, option.service, option.service_name, low, high),
        AddressOut(
            address.name,
            address.email,
            f"{address.phone_dial_code}{address.phone_number}",
            address.line1,
            address.line2,
            address.city,
            address.region,
            address.postcode,
            address.country,
        ),
        invoice_number,
        f"{self_url}/o/{order.buyer_token}",
    )
    return msgspec.json.encode(out)


type Email = Annotated[str, Meta(pattern=r"^[^@\s]+@[^@\s]+$", max_length=254)]


class SettingsIn(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The merchant's own settings; every field is sent, null clears it."""

    support_email: Email | None


class MerchantOut(msgspec.Struct, frozen=True):
    id: str
    object: str
    display_name: str
    kyb_state: str  # ours: only `approved` takes payments
    support_email: str | None


def merchant_out(merchant: Merchant) -> bytes:
    return msgspec.json.encode(
        MerchantOut(
            public_id(MERCHANT_PREFIX, merchant.id),
            "merchant",
            merchant.display_name,
            merchant.kyb_state.value,
            merchant.support_email,
        )
    )
