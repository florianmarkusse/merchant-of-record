"""Carrier accounts with their capabilities, what we ask a carrier for a
price and what we hold of its answers, as plain records the pure quote
reads (docs/plan-quote.md §4, 4c-2; docs/plan-shipments.md §2)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from mor.ids import (
    CarrierAccountId,
    CarrierCode,
    CountryCode,
    MarketLoadId,
    ServiceCode,
    ShippingPriceLookupId,
)
from mor.messaging.guard import DuplicateGuard
from mor.money.amount import Money
from mor.provider_accounts import ProviderAccountKind


@dataclass(frozen=True, kw_only=True)
class Parcel:
    """One parcel per order (4c): its packed weight and box."""

    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


@dataclass(frozen=True, kw_only=True)
class PriceQuestion:
    """A price for one service on one lane, for one exact parcel."""

    carrier: CarrierCode  # the service's carrier, e.g. `dhl_express`
    service: ServiceCode
    origin: CountryCode
    destination: CountryCode
    parcel: Parcel


class CarrierProvider(StrEnum):
    """The module that speaks to a carrier; matches `carrier_provider`. A
    carrier's name (`dhl_express`) is data; its provider is code."""

    FAKE = "fake"


class PriceExpiry(StrEnum):
    """How long a stored price is used; matches `price_expiry` (decided
    19-Nov-2026: reusing a price the carrier calls expired must be a
    switch, not impossible)."""

    CARRIER_FIRST = "carrier_first"  # the earlier of the carrier's and ours
    OWN_PERIOD = "own_period"  # ours, whatever the carrier said


class CarrierTrackingMode(StrEnum):
    """How the carrier tells us what happens to a parcel; matches
    `carrier_tracking_mode`. Used from 6b on (docs/plan-shipments.md §7)."""

    PUSH_FAT = "push_fat"  # signed webhooks carrying the events
    PUSH_THIN = "push_thin"  # webhooks saying only that there is news
    POLL = "poll"  # we fetch pages of events


@dataclass(frozen=True, kw_only=True)
class CarrierAccount:
    """One account per carrier. Its capabilities are data, so feature code
    switches on them, never on the provider (docs/plan-shipments.md §2)."""

    id: CarrierAccountId
    carrier: CarrierCode
    provider: CarrierProvider
    kind: ProviderAccountKind
    base_url: str
    timeout: timedelta
    price_valid_for: timedelta
    price_expiry: PriceExpiry
    book_guard: DuplicateGuard
    tracking_mode: CarrierTrackingMode


class PricePartKind(StrEnum):
    """Matches `carrier_price_part`."""

    FREIGHT = "freight"
    FUEL = "fuel"
    SURCHARGE = "surcharge"


@dataclass(frozen=True)
class PricePart:
    kind: PricePartKind
    name: str  # as the carrier names it
    amount_minor: int  # in the price's currency


class PriceState(StrEnum):
    """Matches `shipping_price_state`."""

    REQUESTED = "requested"
    ANSWERED = "answered"
    NOT_SERVED = "not_served"
    NEEDS_PERSON = "needs_person"


@dataclass(frozen=True)
class OwnPrice:
    """What our own card charged for the same service, lane and parcel when
    the carrier answered, in the carrier's currency: freight, the
    surcharges that always apply, and fuel."""

    load_id: MarketLoadId
    rate_card: str
    total: Money


@dataclass(frozen=True, kw_only=True)
class StoredPrice:
    total: Money
    parts: tuple[PricePart, ...]  # in the total's currency; they sum to it
    carrier_valid_until: datetime | None
    own: OwnPrice | None


@dataclass(frozen=True, kw_only=True)
class FinishedLookup:
    """A lookup that left `requested`: a price (`price` set), not served or
    needs a person (`detail`). `usable_until` is worked out when it is read,
    by the account's rule in force then (`usable_until`)."""

    id: ShippingPriceLookupId
    state: PriceState
    calls: int
    detail: str | None
    finished_at: datetime
    usable_until: datetime
    price: StoredPrice | None


@dataclass(frozen=True)
class PriceHeld:
    """What we hold for a question: the newest finished lookup, and the
    request still open, if any."""

    latest: FinishedLookup | None
    open_id: ShippingPriceLookupId | None


def usable_until(
    finished_at: datetime,
    carrier_valid_until: datetime | None,
    valid_for: timedelta,
    expiry: PriceExpiry,
) -> datetime:
    """Until when a finished lookup is used: our period from when it
    finished, and under `carrier_first` no later than the carrier's own
    validity. Compared with `now` where it is used, never stored."""
    ours = finished_at + valid_for
    match expiry:
        case PriceExpiry.OWN_PERIOD:
            return ours
        case PriceExpiry.CARRIER_FIRST:
            if carrier_valid_until is not None and carrier_valid_until < ours:
                return carrier_valid_until
            return ours
