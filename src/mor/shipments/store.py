"""Shipments in the database (docs/plan-shipments.md §5): the shipment,
its customs lines, its requests to the carrier and its documents.
Persistence functions take the connection first and never commit."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from psycopg.rows import class_row

from mor.carriers.port import Incoterm, Label
from mor.carriers.records import Parcel
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.ids import (
    CarrierAccountId,
    CarrierCode,
    CarrierIdempotencyKey,
    CarrierRequestId,
    CheckoutAddressId,
    CountryCode,
    ExportDeclarationNo,
    HsCode,
    MarketLoadId,
    MerchantId,
    MerchantShipFromId,
    OrderId,
    ServiceCode,
    ShipmentId,
    TrackingNumber,
)
from mor.messaging.guard import VendorRequestState
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.shipments.records import (
    BookingState,
    CarrierOperation,
    DocumentKind,
    LockedShipment,
    Shipment,
    ShipmentFailure,
    TrackingStatus,
)


PDF = "application/pdf"


@dataclass(frozen=True, kw_only=True)
class _ShipmentRow:
    id: ShipmentId
    order_id: OrderId
    sequence: int
    carrier: CarrierCode
    carrier_account_id: CarrierAccountId | None
    service: ServiceCode
    market_load_id: MarketLoadId
    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int
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


def _shipment(row: _ShipmentRow) -> Shipment:
    return Shipment(
        id=row.id,
        order_id=row.order_id,
        sequence=row.sequence,
        carrier=row.carrier,
        carrier_account_id=row.carrier_account_id,
        service=row.service,
        market_load_id=row.market_load_id,
        parcel=Parcel(
            weight_g=row.weight_g,
            length_mm=row.length_mm,
            width_mm=row.width_mm,
            height_mm=row.height_mm,
        ),
        ship_from_id=row.ship_from_id,
        receiver_address_id=row.receiver_address_id,
        incoterm=row.incoterm,
        booking_state=row.booking_state,
        failure=row.failure,
        detail=row.detail,
        tracking_status=row.tracking_status,
        tracking_number=row.tracking_number,
        export_declaration_no=row.export_declaration_no,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@dataclass(frozen=True, kw_only=True)
class NewShipment:
    """What `insert_shipment` writes: the shipment as it starts."""

    order_id: OrderId
    sequence: int
    carrier: CarrierCode
    carrier_account_id: CarrierAccountId | None
    service: ServiceCode
    market_load_id: MarketLoadId
    parcel: Parcel
    ship_from_id: MerchantShipFromId
    receiver_address_id: CheckoutAddressId
    incoterm: Incoterm
    booking_state: Literal[BookingState.BOOKING, BookingState.LABEL_FAILED]
    failure: ShipmentFailure | None
    detail: str | None


@dataclass(frozen=True, kw_only=True)
class CustomsLine:
    description: str
    hs_code: HsCode
    origin: CountryCode
    quantity: int
    value: Money


async def insert_shipment(
    conn: Connection, new: NewShipment, now: datetime
) -> ShipmentId | None:
    """The shipment, unless the order step has one: none then (its unique
    key decides, never a read first)."""
    shipment_id = ShipmentId(uuid.uuid7())
    parcel = new.parcel
    cur = await conn.execute(
        """
        INSERT INTO shipment
          (id, order_id, sequence, carrier, carrier_account_id, service,
           market_load_id, weight_g, length_mm, width_mm, height_mm,
           ship_from_id, receiver_address_id, incoterm, booking_state, failure,
           detail, tracking_status, created_at, updated_at)
        VALUES (%(id)s, %(order_id)s, %(sequence)s, %(carrier)s,
                %(account_id)s, %(service)s, %(load_id)s, %(weight_g)s,
                %(length_mm)s, %(width_mm)s, %(height_mm)s, %(ship_from_id)s,
                %(address_id)s, %(incoterm)s, %(booking_state)s, %(failure)s,
                %(detail)s, %(tracking_status)s, %(now)s, %(now)s)
        ON CONFLICT (order_id, sequence) DO NOTHING
        RETURNING id
        """,
        {
            "id": shipment_id,
            "order_id": new.order_id,
            "sequence": new.sequence,
            "carrier": new.carrier,
            "account_id": new.carrier_account_id,
            "service": new.service,
            "load_id": new.market_load_id,
            "weight_g": parcel.weight_g,
            "length_mm": parcel.length_mm,
            "width_mm": parcel.width_mm,
            "height_mm": parcel.height_mm,
            "ship_from_id": new.ship_from_id,
            "address_id": new.receiver_address_id,
            "incoterm": new.incoterm,
            "booking_state": new.booking_state,
            "failure": new.failure,
            "detail": new.detail,
            "tracking_status": TrackingStatus.NONE,
            "now": now,
        },
    )
    if await cur.fetchone() is None:
        return None
    return shipment_id


async def insert_customs_lines(
    conn: Connection, shipment_id: ShipmentId, lines: list[CustomsLine]
) -> None:
    for line_no, line in enumerate(lines):
        await conn.execute(
            """
            INSERT INTO shipment_customs_line
              (shipment_id, line_no, description, hs_code, origin, quantity,
               value_minor, currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                shipment_id,
                line_no,
                line.description,
                line.hs_code,
                line.origin,
                line.quantity,
                line.value.amount_minor,
                line.value.currency,
            ),
        )


@dataclass(frozen=True, kw_only=True)
class _CustomsRow:
    description: str
    hs_code: HsCode
    origin: CountryCode
    quantity: int
    value_minor: int
    currency: Currency


async def customs_lines(conn: Connection, shipment_id: ShipmentId) -> list[CustomsLine]:
    async with conn.cursor(row_factory=class_row(_CustomsRow)) as cur:
        await cur.execute(
            """
            SELECT description, hs_code, origin, quantity, value_minor, currency
            FROM shipment_customs_line WHERE shipment_id = %s ORDER BY line_no
            """,
            (shipment_id,),
        )
        rows = await cur.fetchall()
    lines: list[CustomsLine] = []
    for row in rows:
        lines.append(
            CustomsLine(
                description=row.description,
                hs_code=row.hs_code,
                origin=row.origin,
                quantity=row.quantity,
                value=Money(row.value_minor, row.currency),
            )
        )
    return lines


async def lock_shipment(conn: Connection, shipment_id: ShipmentId) -> LockedShipment:
    """The shipment under its row lock, for a step that changes it. Every
    caller holds an id a fact or a page named, which a foreign key keeps."""
    async with conn.cursor(row_factory=class_row(_ShipmentRow)) as cur:
        await cur.execute(
            """
            SELECT id, order_id, sequence, carrier, carrier_account_id, service,
                   market_load_id, weight_g, length_mm, width_mm, height_mm,
                   ship_from_id, receiver_address_id, incoterm, booking_state,
                   failure, detail, tracking_status, tracking_number,
                   export_declaration_no, created_at, updated_at
            FROM shipment WHERE id = %s FOR UPDATE
            """,
            (shipment_id,),
        )
        row = await one_row(cur, f"shipment {shipment_id}")
    return LockedShipment(_shipment(row))


async def order_shipment(conn: Connection, order_id: OrderId) -> Shipment | None:
    """The order's outbound shipment, once it exists."""
    async with conn.cursor(row_factory=class_row(_ShipmentRow)) as cur:
        await cur.execute(
            """
            SELECT id, order_id, sequence, carrier, carrier_account_id, service,
                   market_load_id, weight_g, length_mm, width_mm, height_mm,
                   ship_from_id, receiver_address_id, incoterm, booking_state,
                   failure, detail, tracking_status, tracking_number,
                   export_declaration_no, created_at, updated_at
            FROM shipment WHERE order_id = %s AND sequence = 0
            """,
            (order_id,),
        )
        row = await cur.fetchone()
    return None if row is None else _shipment(row)


async def set_booking(
    conn: Connection,
    shipment: LockedShipment,
    state: BookingState,
    now: datetime,
    *,
    failure: ShipmentFailure | None = None,
    detail: str | None = None,
    tracking_number: TrackingNumber | None = None,
    export_declaration_no: ExportDeclarationNo | None = None,
    carrier_account_id: CarrierAccountId | None = None,
) -> None:
    """The booking state, with what goes with it. The numbers and the
    account stay as they are unless given."""
    await conn.execute(
        """
        UPDATE shipment
        SET booking_state = %(state)s, failure = %(failure)s,
            detail = %(detail)s,
            tracking_number = coalesce(%(tracking_number)s, tracking_number),
            export_declaration_no
              = coalesce(%(export_declaration_no)s, export_declaration_no),
            carrier_account_id
              = coalesce(%(carrier_account_id)s, carrier_account_id),
            updated_at = %(now)s
        WHERE id = %(id)s
        """,
        {
            "state": state,
            "failure": failure,
            "detail": detail,
            "tracking_number": tracking_number,
            "export_declaration_no": export_declaration_no,
            "carrier_account_id": carrier_account_id,
            "now": now,
            "id": shipment.id,
        },
    )


async def store_documents(
    conn: Connection, shipment_id: ShipmentId, label: Label, now: datetime
) -> None:
    """The kept label's documents, once: a shipment keeps one label."""
    for kind, body in (
        (DocumentKind.LABEL, label.label_pdf),
        (DocumentKind.COMMERCIAL_INVOICE, label.commercial_invoice_pdf),
    ):
        await conn.execute(
            """
            INSERT INTO shipment_document
              (shipment_id, kind, content_type, body, created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (shipment_id, kind, PDF, body, now),
        )


@dataclass(frozen=True, kw_only=True)
class Document:
    content_type: str
    body: bytes


async def merchant_document(
    conn: Connection, merchant_id: MerchantId, order_id: OrderId, kind: DocumentKind
) -> Document | None:
    """The document of a merchant's own order's shipment; another
    merchant's reads as none."""
    async with conn.cursor(row_factory=class_row(Document)) as cur:
        await cur.execute(
            """
            SELECT d.content_type, d.body
            FROM shipment_document d
            JOIN shipment s ON s.id = d.shipment_id
            JOIN order_ o ON o.id = s.order_id
            WHERE o.id = %s AND o.merchant_id = %s AND s.sequence = 0
              AND d.kind = %s
            """,
            (order_id, merchant_id, kind),
        )
        return await cur.fetchone()


# --- requests to the carrier ----------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CarrierRequest:
    id: CarrierRequestId
    number: int
    idempotency_key: CarrierIdempotencyKey
    state: VendorRequestState
    sends: int
    target_tracking_number: TrackingNumber | None


async def latest_request(
    conn: Connection, shipment_id: ShipmentId, operation: CarrierOperation
) -> CarrierRequest | None:
    async with conn.cursor(row_factory=class_row(CarrierRequest)) as cur:
        await cur.execute(
            """
            SELECT id, number, idempotency_key, state, sends,
                   target_tracking_number
            FROM carrier_request
            WHERE shipment_id = %s AND operation = %s
            ORDER BY number DESC LIMIT 1
            """,
            (shipment_id, operation),
        )
        return await cur.fetchone()


async def new_request(
    conn: Connection,
    shipment_id: ShipmentId,
    operation: CarrierOperation,
    number: int,
    target: TrackingNumber | None,
    now: datetime,
) -> CarrierRequest:
    request_id = CarrierRequestId(uuid.uuid7())
    key = CarrierIdempotencyKey(f"ship:{shipment_id}:{operation}:{number}")
    await conn.execute(
        """
        INSERT INTO carrier_request
          (id, shipment_id, operation, number, idempotency_key, state,
           target_tracking_number, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            request_id,
            shipment_id,
            operation,
            number,
            key,
            VendorRequestState.NOT_SENT,
            target,
            now,
        ),
    )
    return CarrierRequest(
        id=request_id,
        number=number,
        idempotency_key=key,
        state=VendorRequestState.NOT_SENT,
        sends=0,
        target_tracking_number=target,
    )


async def mark_sent(conn: Connection, request_id: CarrierRequestId) -> None:
    """The committed "we are about to ask" mark, before any call."""
    await conn.execute(
        "UPDATE carrier_request SET state = %s, sends = sends + 1 WHERE id = %s",
        (VendorRequestState.SENT, request_id),
    )


async def finish_request(
    conn: Connection,
    request_id: CarrierRequestId,
    state: Literal[VendorRequestState.SUCCEEDED, VendorRequestState.REFUSED],
    now: datetime,
    result: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE carrier_request SET state = %s, result = %s, completed_at = %s
        WHERE id = %s
        """,
        (state, result, now, request_id),
    )


async def unsend_request(conn: Connection, request_id: CarrierRequestId) -> None:
    """The carrier said the call did nothing (Unavailable): the request
    goes back to `not_sent`, and the retry sends it with the same key."""
    await conn.execute(
        "UPDATE carrier_request SET state = %s WHERE id = %s",
        (VendorRequestState.NOT_SENT, request_id),
    )


# --- the staff page -----------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ShipmentSummary:
    """One row of the staff page's shipment list."""

    id: ShipmentId
    order_id: OrderId
    merchant_name: str
    carrier: CarrierCode
    service: ServiceCode
    incoterm: Incoterm
    booking_state: BookingState
    failure: ShipmentFailure | None
    detail: str | None
    tracking_status: TrackingStatus
    tracking_number: TrackingNumber | None
    book_requests: int
    updated_at: datetime


async def recent_shipments(conn: Connection, limit: int) -> list[ShipmentSummary]:
    """The newest shipments first, and every one waiting on a person
    whatever its age."""
    async with conn.cursor(row_factory=class_row(ShipmentSummary)) as cur:
        await cur.execute(
            """
            SELECT s.id, s.order_id, m.display_name AS merchant_name, s.carrier,
                   s.service, s.incoterm, s.booking_state, s.failure, s.detail,
                   s.tracking_status, s.tracking_number,
                   (SELECT count(*) FROM carrier_request r
                    WHERE r.shipment_id = s.id AND r.operation = 'book')::int
                     AS book_requests,
                   s.updated_at
            FROM shipment s
            JOIN order_ o ON o.id = s.order_id
            JOIN merchant m ON m.id = o.merchant_id
            WHERE s.booking_state IN ('label_failed', 'needs_person')
               OR s.id IN (SELECT id FROM shipment ORDER BY id DESC LIMIT %s)
            ORDER BY (s.booking_state IN ('label_failed', 'needs_person')) DESC,
                     s.id DESC
            """,
            (limit,),
        )
        return await cur.fetchall()
