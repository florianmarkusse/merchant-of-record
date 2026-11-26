"""Booking a paid order's label (docs/plan-shipments.md §6).

    order_paid ─▶ shipment_create (local, the order's lock): the shipment
        `booking`, its customs lines, book request #1 not sent;
        shipment_created. A ship-from outside the service's origin, or a
        carrier we hold no account with: `label_failed` at once, no request.
    shipment_created | shipment_booking_retried ─▶ carrier_book (effect,
        the shipment's lock)

After a `sent` book request with no answer, the account's `book_guard`
decides, as for a payment: `vendor_constraint` sends again with the same
key; `compensable` sends again and then moves to `deduplicating`, where
every label the carrier holds for the shipment but the one kept is
cancelled; `none` only looks the label up, and one the carrier does not
show goes to a person.

    booking ──book──▶ labelled | label_failed
        ├─ compensable, sent twice ─▶ deduplicating ─find, cancel extras─▶
        │                                labelled | label_failed
        └─ none, answer lost ─find─▶ labelled | needs_person

`carrier_book` is three steps run by `mor.messaging.delivery.run_steps`:
`prepare_book` and `complete_book` in their transactions under the
shipment's row lock (they never commit), and the call between them with
no transaction open.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.carriers import registry
from mor.carriers.accounts import carrier_account, read_carrier_account
from mor.carriers.port import (
    BookRefusal,
    BookRefused,
    BookRequest,
    BookResult,
    Cancelled,
    CancelResult,
    CustomsItem,
    FindResult,
    Found,
    Incoterm,
    Label,
    LabelState,
    Party,
)
from mor.carriers.records import CarrierAccount
from mor.checkout.addresses import Address
from mor.checkout.quotes import StoredOption, read_made_quote
from mor.checkout.records import SoldLine
from mor.checkout.sessions import read_address, session_by_id
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.ids import (
    CarrierCode,
    CarrierIdempotencyKey,
    CarrierRequestId,
    CountryCode,
    MarketLoadId,
    ServiceCode,
    ShipmentId,
    TrackingNumber,
)
from mor.markets.records import Collector
from mor.merchants.ship_from import ShipFrom, read_ship_from, ship_from_in_force
from mor.messaging.facts import (
    ShipmentBookingRetriedV1,
    ShipmentCreatedV1,
    ShipmentLabelBookedV1,
    ShipmentLabelFailedV1,
    ShipmentNeedsPersonV1,
)
from mor.messaging.guard import DuplicateGuard, VendorRequestState
from mor.messaging.outcome import (
    Again,
    Applied,
    ConsumerOutcome,
    Done,
    NoCall,
    RetryLater,
    Stale,
)
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.orders.store import LockedOrder
from mor.shipments.records import (
    OUTBOUND,
    BookingState,
    CarrierOperation,
    LockedShipment,
    ShipmentFailure,
)
from mor.shipments.store import (
    CustomsLine,
    NewShipment,
    customs_lines,
    finish_request,
    insert_customs_lines,
    insert_shipment,
    latest_request,
    lock_shipment,
    mark_sent,
    new_request,
    set_booking,
    store_documents,
    unsend_request,
)
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


CREATE_ACTOR = Actor(ActorKind.SYSTEM, "shipment_create")
BOOK_ACTOR = Actor(ActorKind.SYSTEM, "carrier_book")


# --- pure rules -------------------------------------------------------------


def incoterm_for(option: StoredOption) -> Incoterm:
    """DDP when the buyer paid us something the border collects: the quote
    offers that only on a duty-paid service, whose carrier pays it and
    bills us. Otherwise nothing is due at the border, and the label says
    so."""
    for part in option.parts:
        if part.collector == Collector.BORDER:
            return Incoterm.DDP
    return Incoterm.DAP


def customs_from(lines: tuple[SoldLine, ...]) -> list[CustomsLine]:
    """Every sold line at its whole value, under our HS code."""
    customs: list[CustomsLine] = []
    for line in lines:
        customs.append(
            CustomsLine(
                description=line.customs.description,
                hs_code=line.customs.hs_code,
                origin=line.customs.origin_country,
                quantity=line.quantity,
                value=Money(
                    line.unit_price.amount_minor * line.quantity,
                    line.unit_price.currency,
                ),
            )
        )
    return customs


@dataclass(frozen=True)
class CannotBook:
    failure: Literal[
        ShipmentFailure.SHIP_FROM_ELSEWHERE, ShipmentFailure.NO_CARRIER_ACCOUNT
    ]
    detail: str


def cannot_book(
    ship_from: CountryCode,
    service: ServiceCode,
    origin: CountryCode,
    account: CarrierAccount | None,
) -> CannotBook | None:
    """What stops a booking before the carrier is asked: a carrier may have
    no account in this environment, and the address can change between the
    quote and the payment. The account first: only a shipment without one
    may lack it (a CHECK), and once it exists, booking again meets the
    ship-from."""
    if account is None:
        return CannotBook(
            ShipmentFailure.NO_CARRIER_ACCOUNT, f"no account for {service}'s carrier"
        )
    if ship_from != origin:
        return CannotBook(
            ShipmentFailure.SHIP_FROM_ELSEWHERE,
            f"ships from {ship_from}; {service} leaves from {origin}",
        )
    return None


# --- reads --------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _ServiceRow:
    carrier: CarrierCode
    origin_iso2: CountryCode


async def _service(
    conn: Connection, load_id: MarketLoadId, service: ServiceCode
) -> _ServiceRow:
    async with conn.cursor(row_factory=class_row(_ServiceRow)) as cur:
        await cur.execute(
            """
            SELECT carrier, origin_iso2 FROM shipping_service
            WHERE load_id = %s AND code = %s
            """,
            (load_id, service),
        )
        return await one_row(cur, f"service {service} in load {load_id}")


# --- shipment_create ----------------------------------------------------------


async def create_shipment(
    conn: Connection, paid_order: LockedOrder, now: datetime
) -> ConsumerOutcome:
    """Consumer `shipment_create`, the order locked: one outbound shipment
    per order (its unique key)."""
    session = await session_by_id(conn, paid_order.session_id)
    assert session is not None  # the order's foreign key
    quote = await read_made_quote(conn, paid_order.quote_id)
    option = quote.options[paid_order.option_no]
    service = await _service(conn, quote.load_id, option.service)
    # Checkout refuses a merchant without one, and the rows are
    # append-only, so a paid order's merchant has one in force.
    ship_from = await ship_from_in_force(conn, paid_order.merchant_id)
    assert ship_from is not None, f"no ship-from for {paid_order.merchant_id}"
    account = await carrier_account(conn, service.carrier)
    blocked = cannot_book(
        ship_from.country, option.service, service.origin_iso2, account
    )
    shipment_id = await insert_shipment(
        conn,
        NewShipment(
            order_id=paid_order.id,
            sequence=OUTBOUND,
            carrier=service.carrier,
            carrier_account_id=None if account is None else account.id,
            service=option.service,
            market_load_id=quote.load_id,
            parcel=session.parcel,
            ship_from_id=ship_from.id,
            receiver_address_id=paid_order.address_id,
            incoterm=incoterm_for(option),
            booking_state=(
                BookingState.BOOKING if blocked is None else BookingState.LABEL_FAILED
            ),
            failure=None if blocked is None else blocked.failure,
            detail=None if blocked is None else blocked.detail,
        ),
        now,
    )
    if shipment_id is None:
        return Stale()  # made already
    await insert_customs_lines(conn, shipment_id, customs_from(session.lines))
    if blocked is not None:
        await append_fact(
            conn,
            ShipmentLabelFailedV1(
                shipment_id=shipment_id,
                order_id=paid_order.id,
                failure=blocked.failure,
            ),
            CREATE_ACTOR,
            now,
        )
        return Applied()
    await new_request(conn, shipment_id, CarrierOperation.BOOK, 1, None, now)
    await append_fact(
        conn,
        ShipmentCreatedV1(shipment_id=shipment_id, order_id=paid_order.id),
        CREATE_ACTOR,
        now,
    )
    return Applied()


# --- book again (staff) ---------------------------------------------------------


@dataclass(frozen=True)
class BookingRetried:
    pass


type NotRetryableReason = Literal[
    "not_stuck", "ship_from_elsewhere", "no_carrier_account"
]


@dataclass(frozen=True)
class NotRetryable:
    reason: NotRetryableReason


async def book_again(
    conn: Connection, shipment_id: ShipmentId, actor: Actor, now: datetime
) -> BookingRetried | NotRetryable:
    """A person books a failed or stuck shipment again: the next book
    request, and `booking`. On a `none` guard after a lost answer the
    person accepts that the carrier may hold a label already. Never
    commits."""
    shipment = await lock_shipment(conn, shipment_id)
    match shipment.booking_state:
        case BookingState.LABEL_FAILED | BookingState.NEEDS_PERSON:
            pass
        case BookingState.BOOKING | BookingState.DEDUPLICATING | BookingState.LABELLED:
            return NotRetryable("not_stuck")
    ship_from = await read_ship_from(conn, shipment.ship_from_id)
    assert ship_from is not None  # the shipment's foreign key
    service = await _service(conn, shipment.market_load_id, shipment.service)
    account = await carrier_account(conn, shipment.carrier)
    blocked = cannot_book(
        ship_from.country, shipment.service, service.origin_iso2, account
    )
    if blocked is not None:
        match blocked.failure:
            case ShipmentFailure.SHIP_FROM_ELSEWHERE:
                return NotRetryable("ship_from_elsewhere")
            case ShipmentFailure.NO_CARRIER_ACCOUNT:
                return NotRetryable("no_carrier_account")
    assert account is not None  # `cannot_book` said so
    last = await latest_request(conn, shipment.id, CarrierOperation.BOOK)
    number = 1 if last is None else last.number + 1
    await new_request(conn, shipment.id, CarrierOperation.BOOK, number, None, now)
    await set_booking(
        conn, shipment, BookingState.BOOKING, now, carrier_account_id=account.id
    )
    await append_fact(
        conn,
        ShipmentBookingRetriedV1(shipment_id=shipment.id, order_id=shipment.order_id),
        actor,
        now,
    )
    return BookingRetried()


# --- carrier_book: calls, what prepare asks for and call carries out --------


@dataclass(frozen=True)
class BookCall:
    account: CarrierAccount
    request_id: CarrierRequestId
    request: BookRequest


@dataclass(frozen=True)
class FindCall:
    account: CarrierAccount
    reference: str


@dataclass(frozen=True)
class CancelCall:
    account: CarrierAccount
    request_id: CarrierRequestId
    tracking_number: TrackingNumber


type CarrierCall = BookCall | FindCall | CancelCall


@dataclass(frozen=True)
class BookAnswer:
    call: BookCall
    result: BookResult


@dataclass(frozen=True)
class FindAnswer:
    call: FindCall
    result: FindResult


@dataclass(frozen=True)
class CancelAnswer:
    call: CancelCall
    result: CancelResult


type CarrierAnswer = BookAnswer | FindAnswer | CancelAnswer


def _party_from_ship_from(ship_from: ShipFrom) -> Party:
    return Party(
        name=ship_from.name,
        email=ship_from.email,
        phone=ship_from.phone_dial_code + ship_from.phone_number,
        line1=ship_from.line1,
        line2=ship_from.line2,
        city=ship_from.city,
        region=ship_from.region,
        postcode=ship_from.postcode,
        country=ship_from.country,
    )


def _party_from_address(address: Address) -> Party:
    return Party(
        name=address.name,
        email=address.email,
        phone=address.phone_dial_code + address.phone_number,
        line1=address.line1,
        line2=address.line2,
        city=address.city,
        region=address.region,
        postcode=address.postcode,
        country=address.country,
    )


async def _account(conn: Connection, shipment: LockedShipment) -> CarrierAccount:
    # Only a shipment that failed for want of one has no account (a CHECK).
    assert shipment.carrier_account_id is not None, shipment.id
    account = await read_carrier_account(conn, shipment.carrier_account_id)
    assert account is not None  # the shipment's foreign key
    return account


async def _book_request(
    conn: Connection, shipment: LockedShipment, key: CarrierIdempotencyKey
) -> BookRequest:
    ship_from = await read_ship_from(conn, shipment.ship_from_id)
    assert ship_from is not None  # the shipment's foreign key
    receiver = await read_address(conn, shipment.receiver_address_id)
    assert receiver is not None  # the shipment's foreign key
    items: list[CustomsItem] = []
    for line in await customs_lines(conn, shipment.id):
        items.append(
            CustomsItem(
                description=line.description,
                hs_code=line.hs_code,
                origin=line.origin,
                quantity=line.quantity,
                value=line.value,
            )
        )
    return BookRequest(
        reference=str(shipment.id),
        key=key,
        service=shipment.service,
        shipper=_party_from_ship_from(ship_from),
        receiver=_party_from_address(receiver),
        parcel=shipment.parcel,
        incoterm=shipment.incoterm,
        items=tuple(items),
    )


async def prepare_book(
    conn: Connection, shipment_id: ShipmentId, now: datetime
) -> NoCall | CarrierCall:
    shipment = await lock_shipment(conn, shipment_id)
    match shipment.booking_state:
        case (
            BookingState.LABELLED
            | BookingState.LABEL_FAILED
            | BookingState.NEEDS_PERSON
        ):
            return NoCall(Stale())
        case BookingState.DEDUPLICATING:
            account = await _account(conn, shipment)
            cancel = await latest_request(conn, shipment.id, CarrierOperation.CANCEL)
            if cancel is not None and cancel.state in (
                VendorRequestState.NOT_SENT,
                VendorRequestState.SENT,
            ):
                # Cancelling twice leaves a label cancelled: always resend.
                assert cancel.target_tracking_number is not None  # a CHECK
                await mark_sent(conn, cancel.id)
                return CancelCall(account, cancel.id, cancel.target_tracking_number)
            return FindCall(account, str(shipment.id))
        case BookingState.BOOKING:
            return await _prepare_send(conn, shipment)


async def _prepare_send(conn: Connection, shipment: LockedShipment) -> CarrierCall:
    account = await _account(conn, shipment)
    request = await latest_request(conn, shipment.id, CarrierOperation.BOOK)
    # Made with the shipment, or by a person's book again, before `booking`.
    assert request is not None, shipment.id
    match request.state:
        case VendorRequestState.NOT_SENT:
            # New, or the carrier said an earlier send did nothing.
            pass
        case VendorRequestState.SENT:
            # Marked sent, no answer recorded: an earlier run died mid-call
            # or got no answer. Whether to send again is the carrier's
            # promise, as the account states it.
            match account.book_guard:
                case DuplicateGuard.VENDOR_CONSTRAINT | DuplicateGuard.COMPENSABLE:
                    pass
                case DuplicateGuard.NONE:
                    return FindCall(account, str(shipment.id))
        case VendorRequestState.SUCCEEDED | VendorRequestState.REFUSED:
            # An answer moves the shipment past `booking` in the transaction
            # that finishes the request; book again opens a new request.
            raise AssertionError(f"{shipment.id} booking but its request is done")
    await mark_sent(conn, request.id)
    book = await _book_request(conn, shipment, request.idempotency_key)
    return BookCall(account, request.id, book)


async def call_carrier(
    http: httpx2.AsyncClient, environment: Environment, carrier_call: CarrierCall
) -> CarrierAnswer:
    match carrier_call:
        case BookCall(account=account, request=request):
            booked = await registry.book(http, environment, account, request)
            return BookAnswer(carrier_call, booked)
        case FindCall(account=account, reference=reference):
            found = await registry.find_by_reference(
                http, environment, account, reference
            )
            return FindAnswer(carrier_call, found)
        case CancelCall(account=account, tracking_number=tracking_number):
            cancelled = await registry.cancel(
                http, environment, account, tracking_number
            )
            return CancelAnswer(carrier_call, cancelled)


# --- complete -----------------------------------------------------------------


async def complete_book(
    conn: Connection,
    shipment_id: ShipmentId,
    carrier_call: CarrierCall,
    answer: CarrierAnswer,
    now: datetime,
) -> Done | RetryLater | Again:
    """`carrier_call` is the call as one union; each answer holds it typed.
    The worker holds the shipment's advisory lock from prepare to here, and
    a person books again only a shipment no call is open for, so the state
    is the one prepare saw."""
    shipment = await lock_shipment(conn, shipment_id)
    state = shipment.booking_state
    match answer:
        case BookAnswer():
            assert state == BookingState.BOOKING, state
            return await _complete_send(conn, shipment, answer.call, answer.result, now)
        case FindAnswer():
            match state:
                case BookingState.BOOKING:
                    return await _complete_lookup(conn, shipment, answer.result, now)
                case BookingState.DEDUPLICATING:
                    return await _complete_dedupe_find(
                        conn, shipment, answer.result, now
                    )
                case (
                    BookingState.LABELLED
                    | BookingState.LABEL_FAILED
                    | BookingState.NEEDS_PERSON
                ):
                    raise AssertionError(f"find completed for a shipment {state}")
        case CancelAnswer():
            assert state == BookingState.DEDUPLICATING, state
            return await _complete_cancel(
                conn, shipment, answer.call, answer.result, now
            )


async def _labelled(
    conn: Connection, shipment: LockedShipment, label: Label, now: datetime
) -> Done:
    await set_booking(
        conn,
        shipment,
        BookingState.LABELLED,
        now,
        tracking_number=label.tracking_number,
        export_declaration_no=label.export_declaration_no,
    )
    await store_documents(conn, shipment.id, label, now)
    await append_fact(
        conn,
        ShipmentLabelBookedV1(
            shipment_id=shipment.id,
            order_id=shipment.order_id,
            tracking_number=label.tracking_number,
        ),
        BOOK_ACTOR,
        now,
    )
    return Done(Applied())


async def _label_failed(
    conn: Connection,
    shipment: LockedShipment,
    failure: ShipmentFailure,
    detail: str | None,
    now: datetime,
) -> Done:
    await set_booking(
        conn, shipment, BookingState.LABEL_FAILED, now, failure=failure, detail=detail
    )
    await append_fact(
        conn,
        ShipmentLabelFailedV1(
            shipment_id=shipment.id, order_id=shipment.order_id, failure=failure
        ),
        BOOK_ACTOR,
        now,
    )
    return Done(Applied())


async def _needs_person(
    conn: Connection, shipment: LockedShipment, detail: str, now: datetime
) -> Done:
    """The request stays as it is: a person decides, and may book again."""
    await set_booking(conn, shipment, BookingState.NEEDS_PERSON, now, detail=detail)
    await append_fact(
        conn,
        ShipmentNeedsPersonV1(shipment_id=shipment.id, order_id=shipment.order_id),
        BOOK_ACTOR,
        now,
    )
    return Done(Applied())


def _failure(refusal: BookRefusal) -> ShipmentFailure:
    match refusal:
        case BookRefusal.ADDRESS:
            return ShipmentFailure.ADDRESS_REFUSED
        case BookRefusal.CUSTOMS:
            return ShipmentFailure.CUSTOMS_REFUSED
        case BookRefusal.OTHER:
            return ShipmentFailure.REFUSED


async def _complete_send(
    conn: Connection,
    shipment: LockedShipment,
    book: BookCall,
    result: BookResult,
    now: datetime,
) -> Done | RetryLater | Again:
    request = await latest_request(conn, shipment.id, CarrierOperation.BOOK)
    assert request is not None and request.id == book.request_id
    # Sent more than once to a carrier that may have made a duplicate: look
    # before deciding, whatever this answer was.
    may_duplicate = (
        request.sends > 1 and book.account.book_guard == DuplicateGuard.COMPENSABLE
    )
    match result:
        case Label() as label:
            await finish_request(
                conn,
                request.id,
                VendorRequestState.SUCCEEDED,
                now,
                label.tracking_number,
            )
            if may_duplicate:
                await set_booking(
                    conn,
                    shipment,
                    BookingState.DEDUPLICATING,
                    now,
                    tracking_number=label.tracking_number,
                )
                return Again()
            return await _labelled(conn, shipment, label, now)
        case BookRefused(refusal=refusal, detail=detail):
            await finish_request(
                conn, request.id, VendorRequestState.REFUSED, now, detail
            )
            if may_duplicate:
                # An earlier send may still have made a label: keep it if so.
                await set_booking(
                    conn,
                    shipment,
                    BookingState.DEDUPLICATING,
                    now,
                    failure=_failure(refusal),
                    detail=detail,
                )
                return Again()
            return await _label_failed(conn, shipment, _failure(refusal), detail, now)
        case Unavailable(detail=detail):
            await unsend_request(conn, request.id)
            return RetryLater(f"book unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"book outcome unknown: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(conn, shipment, f"book answer: {detail}", now)


def _live(found: Found) -> list[Label]:
    live: list[Label] = []
    for entry in found.labels:
        match entry.state:
            case LabelState.LIVE:
                live.append(entry.label)
            case LabelState.CANCELLED:
                pass
    return live


async def _complete_lookup(
    conn: Connection, shipment: LockedShipment, result: FindResult, now: datetime
) -> Done | RetryLater:
    """Guard `none`: never resent, so the carrier holds at most the label
    of each request a person allowed."""
    match result:
        case Found():
            live = _live(result)
            if len(live) == 0:
                # Not found proves nothing: it may still be on its way. A
                # person decides; the request stays `sent`.
                return await _needs_person(
                    conn,
                    shipment,
                    "booking answer lost; the carrier shows no label",
                    now,
                )
            if len(live) > 1:
                return await _needs_person(
                    conn, shipment, f"{len(live)} labels at the carrier", now
                )
            request = await latest_request(conn, shipment.id, CarrierOperation.BOOK)
            assert request is not None
            await finish_request(
                conn,
                request.id,
                VendorRequestState.SUCCEEDED,
                now,
                live[0].tracking_number,
            )
            return await _labelled(conn, shipment, live[0], now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"book lookup: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(conn, shipment, f"lookup answer: {detail}", now)


async def _complete_dedupe_find(
    conn: Connection, shipment: LockedShipment, result: FindResult, now: datetime
) -> Done | RetryLater | Again:
    """Keep the label the carrier answered with, or else the oldest live
    one; cancel each other, one request at a time."""
    match result:
        case Found():
            live = _live(result)
            keep = shipment.tracking_number
            if keep is None and live:
                keep = live[0].tracking_number
            kept: Label | None = None
            extras: list[TrackingNumber] = []
            for label in live:
                if label.tracking_number == keep:
                    kept = label
                else:
                    extras.append(label.tracking_number)
            if extras:
                last = await latest_request(conn, shipment.id, CarrierOperation.CANCEL)
                number = 1 if last is None else last.number + 1
                await new_request(
                    conn, shipment.id, CarrierOperation.CANCEL, number, extras[0], now
                )
                return Again()
            if kept is not None:
                return await _labelled(conn, shipment, kept, now)
            if keep is not None:
                return await _needs_person(
                    conn, shipment, f"the carrier no longer shows {keep} live", now
                )
            # Every send was refused and no label exists: the refusal stands.
            assert shipment.failure is not None  # kept while deduplicating
            return await _label_failed(
                conn, shipment, shipment.failure, shipment.detail, now
            )
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"dedupe lookup: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(conn, shipment, f"lookup answer: {detail}", now)


async def _complete_cancel(
    conn: Connection,
    shipment: LockedShipment,
    cancel: CancelCall,
    result: CancelResult,
    now: datetime,
) -> Done | RetryLater | Again:
    match result:
        case Cancelled():
            await finish_request(
                conn, cancel.request_id, VendorRequestState.SUCCEEDED, now
            )
            return Again()
        case Unavailable(detail=detail):
            await unsend_request(conn, cancel.request_id)
            return RetryLater(f"cancel unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"cancel outcome unknown: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn,
                shipment,
                f"cancel of {cancel.tracking_number} answered: {detail}",
                now,
            )
