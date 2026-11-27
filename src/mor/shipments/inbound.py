"""What carriers send us about labels, one path for every mode
(docs/plan-shipments.md §7; docs/plan-messaging.md §7):

    raw bytes (webhook or tracking page) ─▶ store_inbox (unique per account
        and body hash) + carrier_inbox_stored
    carrier_inbox_decode (per account): each event becomes
        carrier_event_received for the shipment whose label has its
        tracking number; a thin notice becomes shipment_events_wanted; one
        for a tracking number no shipment has is a carrier_event_unmatched
        row, once by the carrier's event id, with its fact
    carrier_event_match (on carrier_event_unmatched, and on
        shipment_label_booked): the stored unmatched events of a tracking
        number, once a shipment has it, go the same way

An event can name a label before we know it is ours: the booking's answer
was lost, or extra labels are being sorted out. Both sides may come first,
as a bank credit and its payout do (mor.bank.matching): the unmatched row
and the label's tracking number are each committed with the fact that runs
the match, so whichever commits second sees both. Neither side locks: an
event matched by both is sent twice, and the carrier's event id records it
once (`shipment_event`, step 3).

The guard against a duplicate event is the carrier's event id; the body
hash only means "these bytes are stored already". Matching is by tracking
number, not by our reference: an extra label a `compensable` resend made
carries our reference too, and its events are not the shipment's.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.carriers import registry
from mor.carriers.accounts import read_carrier_account
from mor.carriers.port import CarrierEvent
from mor.carriers.records import CarrierAccount, CarrierTrackingMode
from mor.db.connect import Connection
from mor.db.fetch import one_row, one_value
from mor.ids import (
    CarrierAccountId,
    CarrierEventId,
    CarrierInboxId,
    ShipmentId,
    TrackingNumber,
)
from mor.messaging.facts import (
    CarrierEventReceivedV1,
    CarrierEventUnmatchedV1,
    CarrierInboxStoredV1,
    ShipmentEventsWantedV1,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.store import append_fact
from mor.vendor_failures import UndecodableBody
from mor.vendor_inbox import AlreadyStored, Stored, UndecodableInbox, body_sha256


RECEIVER = Actor(ActorKind.SYSTEM, "carrier_inbox")
DECODER = Actor(ActorKind.SYSTEM, "carrier_inbox_decode")
MATCHER = Actor(ActorKind.SYSTEM, "carrier_event_match")


async def store_inbox(
    conn: Connection,
    account_id: CarrierAccountId,
    mode: CarrierTrackingMode,
    body: bytes,
    now: datetime,
) -> Stored[CarrierInboxId] | AlreadyStored:
    """Keep the raw bytes, nothing decoded, with the fact that starts the
    decoding. Insert, and a conflict means stored already. Never
    commits."""
    inbox_id = CarrierInboxId(uuid.uuid7())
    cur = await conn.execute(
        """
        INSERT INTO carrier_inbox
          (id, account_id, mode, body_sha256, body, received_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, body_sha256) DO NOTHING
        RETURNING id
        """,
        (inbox_id, account_id, mode, body_sha256(body), body, now),
    )
    if await cur.fetchone() is None:
        return AlreadyStored()
    await append_fact(
        conn,
        CarrierInboxStoredV1(carrier_account_id=account_id, inbox_id=inbox_id),
        RECEIVER,
        now,
    )
    return Stored(inbox_id)


@dataclass(frozen=True)
class _InboxRow:
    mode: CarrierTrackingMode
    body: bytes


async def _inbox(conn: Connection, inbox_id: CarrierInboxId) -> _InboxRow:
    async with conn.cursor(row_factory=class_row(_InboxRow)) as cur:
        await cur.execute(
            "SELECT mode, body FROM carrier_inbox WHERE id = %s", (inbox_id,)
        )
        return await one_row(cur, f"carrier inbox row {inbox_id}")


async def _account(conn: Connection, account_id: CarrierAccountId) -> CarrierAccount:
    account = await read_carrier_account(conn, account_id)
    assert account is not None, f"no carrier account {account_id}"
    return account


@dataclass(frozen=True)
class _Labelled:
    id: ShipmentId


async def _shipment_of_label(
    conn: Connection, account_id: CarrierAccountId, tracking_number: TrackingNumber
) -> ShipmentId | None:
    """The shipment whose kept label has this tracking number, if any yet."""
    async with conn.cursor(row_factory=class_row(_Labelled)) as cur:
        await cur.execute(
            """
            SELECT id FROM shipment
            WHERE carrier_account_id = %s AND tracking_number = %s
            """,
            (account_id, tracking_number),
        )
        found = await cur.fetchone()
    return None if found is None else found.id


def _received(
    shipment_id: ShipmentId, event: CarrierEvent, inbox_id: CarrierInboxId
) -> CarrierEventReceivedV1:
    return CarrierEventReceivedV1(
        shipment_id=shipment_id,
        carrier_event_id=event.carrier_event_id,
        kind=event.kind,
        carrier_code=event.carrier_code,
        occurred_at=event.occurred_at,
        inbox_id=inbox_id,
    )


async def _unmatched(
    conn: Connection,
    account_id: CarrierAccountId,
    carrier_event_id: CarrierEventId,
    tracking_number: TrackingNumber,
    inbox_id: CarrierInboxId,
    now: datetime,
) -> None:
    """Keep an event no shipment has yet, once by its id, with the fact
    that matches it: the same event in a later page adds nothing."""
    cur = await conn.execute(
        """
        INSERT INTO carrier_event_unmatched
          (account_id, carrier_event_id, tracking_number, inbox_id, recorded_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (account_id, carrier_event_id) DO NOTHING
        RETURNING carrier_event_id
        """,
        (account_id, carrier_event_id, tracking_number, inbox_id, now),
    )
    if await cur.fetchone() is None:
        return
    await append_fact(
        conn,
        CarrierEventUnmatchedV1(
            carrier_account_id=account_id, tracking_number=tracking_number
        ),
        DECODER,
        now,
    )


async def decode_inbox(
    conn: Connection, stored_fact: CarrierInboxStoredV1, now: datetime
) -> ConsumerOutcome:
    """Consumer `carrier_inbox_decode`, per account: the stored bytes into
    one fact per event, each for its own shipment (one step locks one
    aggregate). Locks nothing: an inbox row never changes, and an
    unmatched row's insert is its own guard."""
    account_id = stored_fact.carrier_account_id
    inbox_id = stored_fact.inbox_id
    stored = await _inbox(conn, inbox_id)
    account = await _account(conn, account_id)

    match stored.mode:
        case CarrierTrackingMode.PUSH_THIN:
            notice = registry.decode_thin_webhook(account, stored.body)
            if isinstance(notice, UndecodableBody):
                raise UndecodableInbox(f"carrier inbox {inbox_id}: {notice.detail}")
            shipment_id = await _shipment_of_label(
                conn, account_id, notice.tracking_number
            )
            if shipment_id is None:
                await _unmatched(
                    conn,
                    account_id,
                    notice.carrier_event_id,
                    notice.tracking_number,
                    inbox_id,
                    now,
                )
                return Applied()
            await append_fact(
                conn, ShipmentEventsWantedV1(shipment_id=shipment_id), DECODER, now
            )
            return Applied()
        case CarrierTrackingMode.PUSH_FAT | CarrierTrackingMode.POLL:
            pass

    events = registry.decode_events(account, stored.body)
    if isinstance(events, UndecodableBody):
        raise UndecodableInbox(f"carrier inbox {inbox_id}: {events.detail}")
    for event in events:
        shipment_id = await _shipment_of_label(conn, account_id, event.tracking_number)
        if shipment_id is None:
            await _unmatched(
                conn,
                account_id,
                event.carrier_event_id,
                event.tracking_number,
                inbox_id,
                now,
            )
            continue
        await append_fact(conn, _received(shipment_id, event, inbox_id), DECODER, now)
    return Applied()


@dataclass(frozen=True)
class _UnmatchedRow:
    carrier_event_id: CarrierEventId
    inbox_id: CarrierInboxId


async def match_unmatched(
    conn: Connection,
    account_id: CarrierAccountId,
    tracking_number: TrackingNumber,
    now: datetime,
) -> ConsumerOutcome:
    """Consumer `carrier_event_match`: the unmatched events of a tracking
    number, once a shipment's label has it, sent on as the decoder would
    have. Each inbox body is decoded again; the raw bytes stay the only
    source. Locks nothing (see the module's docstring)."""
    shipment_id = await _shipment_of_label(conn, account_id, tracking_number)
    if shipment_id is None:
        return Stale()  # not ours yet: the label's own fact matches it later
    async with conn.cursor(row_factory=class_row(_UnmatchedRow)) as cur:
        await cur.execute(
            """
            SELECT carrier_event_id, inbox_id FROM carrier_event_unmatched
            WHERE account_id = %s AND tracking_number = %s
            ORDER BY recorded_at, carrier_event_id
            """,
            (account_id, tracking_number),
        )
        waiting = await cur.fetchall()
    if not waiting:
        return Stale()
    account = await _account(conn, account_id)
    wanted: set[CarrierEventId] = set()
    inbox_ids: list[CarrierInboxId] = []
    for row in waiting:
        wanted.add(row.carrier_event_id)
        if row.inbox_id not in inbox_ids:
            inbox_ids.append(row.inbox_id)
    fetch_wanted = False
    for inbox_id in inbox_ids:
        stored = await _inbox(conn, inbox_id)
        match stored.mode:
            case CarrierTrackingMode.PUSH_THIN:
                # A thin notice says only "news": one fetch finds it all.
                fetch_wanted = True
                continue
            case CarrierTrackingMode.PUSH_FAT | CarrierTrackingMode.POLL:
                pass
        events = registry.decode_events(account, stored.body)
        if isinstance(events, UndecodableBody):
            raise UndecodableInbox(f"carrier inbox {inbox_id}: {events.detail}")
        for event in events:
            if event.carrier_event_id not in wanted:
                continue
            if event.tracking_number != tracking_number:
                continue
            await append_fact(
                conn, _received(shipment_id, event, inbox_id), MATCHER, now
            )
    if fetch_wanted:
        await append_fact(
            conn, ShipmentEventsWantedV1(shipment_id=shipment_id), MATCHER, now
        )
    return Applied()


async def label_account(conn: Connection, shipment_id: ShipmentId) -> CarrierAccountId:
    """The account a labelled shipment was booked with: a label needs one
    (`shipment`'s CHECK)."""
    found = await one_value(
        conn,
        "SELECT carrier_account_id FROM shipment WHERE id = %s",
        (shipment_id,),
        uuid.UUID,
    )
    return CarrierAccountId(found)
