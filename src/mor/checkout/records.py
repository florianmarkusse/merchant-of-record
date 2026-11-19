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
    """Matches the `checkout_session_state` enum type. `quoted`, `paying`
    and `completed` come with the quote flow (4d-2) and payments (slice 5)."""

    OPEN = "open"
    EXPIRED = "expired"
    CANCELED = "canceled"


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
    created_at: datetime
    expires_at: datetime
