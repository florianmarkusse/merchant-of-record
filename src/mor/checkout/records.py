"""Checkout sessions and what was sold (plan-merchant-surfaces §3;
docs/plan-quote.md §5, 4d-1)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from mor.couriers.records import Parcel
from mor.money.amount import Money


SESSION_LIFETIME = timedelta(hours=24)


class SessionState(StrEnum):
    """Matches the `checkout_session_state` enum type (docs/plan-quote.md §5,
    4d-2; docs/plan-payments.md §4)."""

    OPEN = "open"  # no address yet
    QUOTING = "quoting"  # an address, and an input being fetched
    QUOTED = "quoted"  # its quote is made
    REFUSED = "refused"  # its quote is refused
    PAYING = "paying"  # an attempt is open; never expires meanwhile
    COMPLETED = "completed"  # paid: its order exists
    EXPIRED = "expired"
    CANCELED = "canceled"


class QuoteState(StrEnum):
    """Matches the `quote_state` enum type."""

    MADE = "made"
    REFUSED = "refused"


class AfterPayment(StrEnum):
    """Where the buyer lands once paid, as the merchant chose for the
    session (docs/plan-payments.md §6.1). Matches the `after_payment` enum
    type."""

    OUR_PAGE = "our_page"  # the order page, with the invoice
    MERCHANT_PAGE = "merchant_page"  # return_url, with `session_id` added


# A buyer may give this many addresses per session (answered 19-Nov-2026),
# so a script cannot sweep countries through one cart.
MAX_ADDRESSES = 10


@dataclass(frozen=True)
class Customs:
    description: str
    origin_country: str
    weight_g: int  # one unit's
    hs_code: str | None  # the merchant's: a hint, classification is ours


@dataclass(frozen=True)
class SoldLine:
    """A line as the merchant sent it, snapshotted: a later change on the
    merchant's side never changes what was sold."""

    name: str
    quantity: int
    unit_price: Money  # tax-exclusive
    category: str
    merchant_sku: str | None
    customs: Customs


@dataclass(frozen=True)
class SessionDraft:
    lines: tuple[SoldLine, ...]
    parcel: Parcel
    merchant_reference: str | None
    return_url: str
    cancel_url: str
    after_payment: AfterPayment


@dataclass(frozen=True)
class CheckoutSession:
    id: uuid.UUID
    merchant_id: uuid.UUID
    state: SessionState
    lines: tuple[SoldLine, ...]
    parcel: Parcel
    merchant_reference: str | None
    return_url: str
    cancel_url: str
    after_payment: AfterPayment
    created_at: datetime
    expires_at: datetime
    address_id: uuid.UUID | None  # the newest address given
    quote_id: uuid.UUID | None  # its quote, once made or refused
