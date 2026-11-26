"""Shipment closed sets and records apart from the flow, so `mor.db.enums`
and the facts can load them (the flow imports the database).
docs/plan-shipments.md §5."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NewType

from mor.carriers.port import Incoterm
from mor.carriers.records import Parcel
from mor.ids import (
    CarrierAccountId,
    CarrierCode,
    CheckoutAddressId,
    ExportDeclarationNo,
    MarketLoadId,
    MerchantShipFromId,
    OrderId,
    ServiceCode,
    ShipmentId,
    TrackingNumber,
)


class BookingState(StrEnum):
    """Matches `shipment_booking_state`: the machine we drive."""

    BOOKING = "booking"  # asked of the carrier, no label recorded yet
    LABELLED = "labelled"  # one label, its documents stored
    DEDUPLICATING = "deduplicating"  # a resend may have made extra labels
    LABEL_FAILED = "label_failed"  # refused, by the carrier or before asking
    NEEDS_PERSON = "needs_person"  # no answer on `none`, and none found


class TrackingStatus(StrEnum):
    """Matches `shipment_tracking_status`: derived from the carrier's
    events from 6b on, `none` until then."""

    NONE = "none"
    PICKED_UP = "picked_up"
    IN_TRANSIT = "in_transit"
    HELD_AT_CUSTOMS = "held_at_customs"
    DELIVERED = "delivered"
    RETURNING = "returning"
    RETURNED = "returned"
    LOST = "lost"


class ShipmentFailure(StrEnum):
    """Matches `shipment_failure`: why a shipment is `label_failed`."""

    SHIP_FROM_ELSEWHERE = "ship_from_elsewhere"  # not the service's origin
    NO_CARRIER_ACCOUNT = "no_carrier_account"
    ADDRESS_REFUSED = "address_refused"
    CUSTOMS_REFUSED = "customs_refused"
    REFUSED = "refused"


class CarrierOperation(StrEnum):
    """Matches `carrier_operation`."""

    BOOK = "book"
    CANCEL = "cancel"


class DocumentKind(StrEnum):
    """Matches `shipment_document_kind`."""

    LABEL = "label"
    COMMERCIAL_INVOICE = "commercial_invoice"


# The outbound parcel; returns (slice 8) take the next numbers.
OUTBOUND = 0


@dataclass(frozen=True, kw_only=True)
class Shipment:
    id: ShipmentId
    order_id: OrderId
    sequence: int
    carrier: CarrierCode
    carrier_account_id: CarrierAccountId | None  # none: no account yet
    service: ServiceCode
    market_load_id: MarketLoadId
    parcel: Parcel
    ship_from_id: MerchantShipFromId
    receiver_address_id: CheckoutAddressId
    incoterm: Incoterm
    booking_state: BookingState
    failure: ShipmentFailure | None
    detail: str | None
    tracking_status: TrackingStatus
    tracking_number: TrackingNumber | None
    export_declaration_no: ExportDeclarationNo | None
    created_at: datetime
    updated_at: datetime


# A row read under its row lock (`SELECT … FOR UPDATE`), made only by
# the lock function: the functions that change the row take this type, so
# pyright refuses a read-then-write on a row read without its lock
# (docs/plan-messaging.md §5). It says the row was locked when
# read, not that the transaction holding the lock is still open.
LockedShipment = NewType("LockedShipment", Shipment)
