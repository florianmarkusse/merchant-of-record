"""What carriers send us about labels (docs/plan-shipments.md §7, 6b step
2): a webhook in, the stored bytes, the facts for each shipment and the
unmatched events out. Every merchant, buyer and address here is made
up."""

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from psycopg.rows import class_row

from mor.carriers.accounts import create_carrier_account
from mor.carriers.fake_client import SIGNATURE_HEADER
from mor.carriers.port import ShipmentEventKind
from mor.carriers.records import CarrierProvider, CarrierTrackingMode, PriceExpiry
from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.ids import CarrierAccountId, TrackingNumber
from mor.messaging.delivery import Candidate
from mor.messaging.facts import (
    CarrierEventReceivedV1,
    CarrierInboxStoredV1,
    MessageType,
    ShipmentEventsWantedV1,
    UndecodableFact,
    decode,
)
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import Failed, find_candidates, run_one
from mor.provider_accounts import ProviderAccountKind
from mor.seed import carrier_flavour
from mor.shipments.inbound import decode_inbox, store_inbox
from mor.shipments.records import BookingState, Shipment
from mor.vendor_inbox import Stored
from tests.helpers import ENV, VENDOR_TIMEOUT, count_rows
from tests.payments.support import (
    FAT_SALE_KEYED,
    Setup,
    approve_at_the_psp,
    drain,
    drain_all_but_invoices,
    with_setup,
)
from tests.shipments.support import CARRIER, fault, shipment_of_session


CARRIER_SECRET = "whsec-carrier-test"


@dataclass(frozen=True)
class _Carrier:
    id: CarrierAccountId
    base: str  # the fake's path for this account


async def _carrier(
    setup: Setup,
    mode: CarrierTrackingMode,
    guard: DuplicateGuard = DuplicateGuard.VENDOR_CONSTRAINT,
) -> _Carrier:
    """A carrier account in the mode asked for, its webhook subscribed at
    the fake as on the carrier's dashboard."""
    base = f"http://mor/_fake/carrier/{carrier_flavour(guard)}/acct"
    async with await connect(setup.url) as conn:
        account_id = await create_carrier_account(
            conn,
            CARRIER,
            CarrierProvider.FAKE,
            ProviderAccountKind.FAKE,
            base,
            VENDOR_TIMEOUT,
            timedelta(hours=24),
            PriceExpiry.CARRIER_FIRST,
            book_guard=guard,
            tracking_mode=mode,
            webhook_secret=CARRIER_SECRET,
        )
        await conn.commit()
    match mode:
        case CarrierTrackingMode.PUSH_FAT | CarrierTrackingMode.PUSH_THIN:
            payload = "fat" if mode == CarrierTrackingMode.PUSH_FAT else "thin"
            subscribed = await setup.wired.control.post(
                f"{base}/webhooks",
                json={
                    "url": f"http://mor/webhooks/carrier/{account_id}",
                    "secret": CARRIER_SECRET,
                    "payload": payload,
                },
            )
            assert subscribed.status_code == 204, subscribed.text
        case CarrierTrackingMode.POLL:
            pass
    return _Carrier(account_id, base)


async def _labelled(setup: Setup) -> Shipment:
    await approve_at_the_psp(setup)
    await drain(setup)
    shipment = await shipment_of_session(setup)
    assert shipment.booking_state == BookingState.LABELLED
    return shipment


async def _release(
    setup: Setup, carrier: _Carrier, tracking_number: str, push: str = "once"
) -> list[str]:
    """Release the label's whole script at the fake: how each push went."""
    released = await setup.wired.control.post(
        f"{carrier.base}/events/release",
        json={"tracking_number": tracking_number, "all": True, "push": push},
    )
    assert released.status_code == 200, released.text
    pushes: list[str] = []
    for item in released.json()["released"]:
        pushes.append(item["push"])
    return pushes


@dataclass(frozen=True)
class _Message:
    message_type: MessageType
    payload: str


async def _facts(setup: Setup, message_type: MessageType) -> list[object]:
    async with await connect(setup.url) as conn:
        async with conn.cursor(row_factory=class_row(_Message)) as cur:
            await cur.execute(
                """
                SELECT message_type, payload::text AS payload FROM outbox
                WHERE message_type = %s ORDER BY seq
                """,
                (message_type,),
            )
            rows = await cur.fetchall()
    facts: list[object] = []
    for row in rows:
        fact = decode(row.message_type, 1, row.payload.encode())
        assert not isinstance(fact, UndecodableFact), fact
        facts.append(fact)
    return facts


async def _received(setup: Setup) -> list[CarrierEventReceivedV1]:
    received: list[CarrierEventReceivedV1] = []
    for fact in await _facts(setup, MessageType.CARRIER_EVENT_RECEIVED):
        assert isinstance(fact, CarrierEventReceivedV1)
        received.append(fact)
    return received


def _kinds(received: list[CarrierEventReceivedV1]) -> set[ShipmentEventKind]:
    kinds: set[ShipmentEventKind] = set()
    for fact in received:
        kinds.add(fact.kind)
    return kinds


def _signed(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _post_carrier(
    setup: Setup, carrier: _Carrier, body: bytes, secret: str = CARRIER_SECRET
) -> int:
    response = await setup.wired.control.post(
        f"http://mor/webhooks/carrier/{carrier.id}",
        content=body,
        headers={SIGNATURE_HEADER: _signed(body, secret)},
    )
    return response.status_code


def _fat(event_id: str, tracking_number: str, code: str = "picked_up") -> bytes:
    return json.dumps(
        {
            "events": [
                {
                    "id": event_id,
                    "tracking_number": tracking_number,
                    "reference": "not-ours",
                    "code": code,
                    "occurred_at": "2026-11-18T12:00:00Z",
                }
            ]
        }
    ).encode()


def test_fat_pushes_become_one_fact_per_event_for_the_shipment(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        shipment = await _labelled(setup)
        assert shipment.tracking_number is not None
        assert (
            await _release(setup, carrier, shipment.tracking_number)
            == ["delivered"] * 6
        )
        await drain(setup)

        received = await _received(setup)
        assert len(received) == 6
        for fact in received:
            assert fact.shipment_id == shipment.id
        # The fake's codes in our words: out for delivery is in transit.
        assert _kinds(received) == {
            ShipmentEventKind.PICKED_UP,
            ShipmentEventKind.IN_TRANSIT,
            ShipmentEventKind.HELD_AT_CUSTOMS,
            ShipmentEventKind.CUSTOMS_RELEASED,
            ShipmentEventKind.DELIVERED,
        }
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 6
        assert (
            await count_rows(setup.url, "SELECT count(*) FROM carrier_event_unmatched")
            == 0
        )

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_push_sent_twice_is_stored_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        shipment = await _labelled(setup)
        assert shipment.tracking_number is not None
        await setup.wired.control.post(
            f"{carrier.base}/events/release",
            json={"tracking_number": shipment.tracking_number, "push": "twice"},
        )
        await drain(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 1
        assert len(await _received(setup)) == 1

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_badly_signed_push_is_refused_and_redelivered_later(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        shipment = await _labelled(setup)
        assert shipment.tracking_number is not None
        pushes = await _release(
            setup, carrier, shipment.tracking_number, push="bad_signature"
        )
        assert set(pushes) == {"failed"}
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 0

        await setup.wired.control.post(f"{carrier.base}/events/redeliver", json={})
        await drain(setup)
        assert len(await _received(setup)) == 6

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_thin_push_asks_for_the_shipments_events(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_THIN)
        shipment = await _labelled(setup)
        assert shipment.tracking_number is not None
        released = await setup.wired.control.post(
            f"{carrier.base}/events/release",
            json={"tracking_number": shipment.tracking_number},
        )
        assert released.status_code == 200
        await drain(setup)
        wanted = await _facts(setup, MessageType.SHIPMENT_EVENTS_WANTED)
        assert wanted == [ShipmentEventsWantedV1(shipment_id=shipment.id)]
        assert await _received(setup) == []

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_event_before_the_label_is_ours_is_matched_once_it_is(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        # The carrier books, the answer is lost: the label exists at the
        # carrier, we do not know its tracking number yet.
        await fault(setup, "timeout_after_work")
        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        booking = await shipment_of_session(setup)
        assert booking.booking_state == BookingState.BOOKING
        async with await connect(setup.url) as conn:
            found = await one_value(
                conn,
                "SELECT tracking_number FROM fake_vendor.carrier_shipment"
                " WHERE reference = %s",
                (str(booking.id),),
                str,
            )
        tracking_number = TrackingNumber(found)

        # The parcel is picked up and the carrier pushes it at once.
        released = await setup.wired.control.post(
            f"{carrier.base}/events/release",
            json={"tracking_number": tracking_number},
        )
        assert released.status_code == 200
        await drain_all_but_invoices(setup)
        assert (
            await count_rows(setup.url, "SELECT count(*) FROM carrier_event_unmatched")
            == 1
        )
        assert await _received(setup) == []

        # The booking is sent again with its key: the label is ours, and the
        # stored event goes to its shipment.
        await drain(setup)
        labelled = await shipment_of_session(setup)
        assert labelled.booking_state == BookingState.LABELLED
        assert labelled.tracking_number == tracking_number
        received = await _received(setup)
        assert len(received) == 1
        assert received[0].shipment_id == labelled.id
        assert received[0].kind == ShipmentEventKind.PICKED_UP

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


async def _run_only(setup: Setup, consumer: ConsumerName) -> None:
    """The worker, running only `consumer`'s deliveries until none is due."""
    async with await connect(setup.url, autocommit=True) as worker:
        for _ in range(20):
            due: list[Candidate] = []
            for candidate in await find_candidates(worker, setup.clock[0], 50):
                if candidate.consumer == consumer:
                    due.append(candidate)
            if not due:
                return
            for candidate in due:
                result = await run_one(
                    worker, setup.wired.http, ENV, candidate, setup.clock[0]
                )
                assert not isinstance(result, Failed), result
    raise AssertionError(f"{consumer} never caught up")


async def _matched_by_the_match(setup: Setup) -> int:
    """Events the match sent on, not the decoder."""
    return await count_rows(
        setup.url,
        "SELECT count(*) FROM outbox WHERE message_type = 'carrier_event_received'"
        " AND actor_id = 'carrier_event_match'",
    )


def test_an_event_decoded_while_its_label_commits_is_matched_by_its_own_fact(
    database_url: str,
) -> None:
    """The order the bank pattern exists for: the decoder looks before the
    label's tracking number commits and stores the event after, so the
    label's own match finds nothing and the stored event's fact must."""

    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.POLL)
        await fault(setup, "timeout_after_work")
        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        booking = await shipment_of_session(setup)
        assert booking.booking_state == BookingState.BOOKING
        async with await connect(setup.url) as conn:
            found = await one_value(
                conn,
                "SELECT tracking_number FROM fake_vendor.carrier_shipment"
                " WHERE reference = %s",
                (str(booking.id),),
                str,
            )
        released = await setup.wired.control.post(
            f"{carrier.base}/events/release", json={"tracking_number": found}
        )
        assert released.status_code == 200
        page = await setup.wired.control.get(
            f"{carrier.base}/tracking", params={"tracking_number": found}
        )
        assert page.status_code == 200
        async with await connect(setup.url) as conn:
            stored = await store_inbox(
                conn, carrier.id, CarrierTrackingMode.POLL, page.content, setup.clock[0]
            )
            await conn.commit()
        assert isinstance(stored, Stored)

        # The decoder's transaction: no shipment has the number yet, so the
        # event is kept unmatched; not committed yet.
        async with await connect(setup.url) as decoding:
            await decode_inbox(
                decoding,
                CarrierInboxStoredV1(
                    carrier_account_id=carrier.id, inbox_id=stored.inbox_id
                ),
                setup.clock[0],
            )

            # Meanwhile the booking is sent again and the label is ours; its
            # match runs and sees no unmatched event yet.
            setup.clock[0] += timedelta(minutes=10)
            await _run_only(setup, ConsumerName.CARRIER_BOOK)
            labelled = await shipment_of_session(setup)
            assert labelled.booking_state == BookingState.LABELLED
            assert labelled.tracking_number == found
            await _run_only(setup, ConsumerName.CARRIER_EVENT_MATCH)
            assert await _received(setup) == []

            await decoding.commit()

        # The stored event's own fact runs the match, which now sees both.
        await _run_only(setup, ConsumerName.CARRIER_EVENT_MATCH)
        assert await _matched_by_the_match(setup) == 1
        (received,) = await _received(setup)
        assert received.shipment_id == labelled.id
        assert received.kind == ShipmentEventKind.PICKED_UP

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_event_for_a_label_never_ours_is_kept_once_for_a_person(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        assert await _post_carrier(setup, carrier, _fat("TE-1", "TN-NOT-OURS")) == 200
        # The same event again, in another body (a page of it).
        again = _fat("TE-1", "TN-NOT-OURS").replace(b"not-ours", b"still-not-ours")
        assert await _post_carrier(setup, carrier, again) == 200
        await drain(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 2
        assert (
            await count_rows(setup.url, "SELECT count(*) FROM carrier_event_unmatched")
            == 1
        )
        assert await _received(setup) == []

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_unknown_carrier_code_is_kept_as_other(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        shipment = await _labelled(setup)
        assert shipment.tracking_number is not None
        event = _fat("TE-NEW", shipment.tracking_number, code="weather_delay")
        assert await _post_carrier(setup, carrier, event) == 200
        await drain(setup)
        (received,) = await _received(setup)
        assert received.kind == ShipmentEventKind.OTHER
        assert received.carrier_code == "weather_delay"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_webhooks_for_no_pushing_account_or_badly_signed_are_refused(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        unknown = await setup.wired.control.post(
            f"http://mor/webhooks/carrier/{uuid.uuid7()}", content=b"{}"
        )
        assert unknown.status_code == 404
        polling = await _carrier(setup, CarrierTrackingMode.POLL)
        assert await _post_carrier(setup, polling, _fat("TE-1", "TN-1")) == 404
        async with await connect(setup.url) as conn:
            await conn.execute(
                "UPDATE carrier_account SET tracking_mode = 'push_fat' WHERE id = %s",
                (polling.id,),
            )
            await conn.commit()
        wrong = await _post_carrier(
            setup, polling, _fat("TE-1", "TN-1"), secret="not-the-secret"
        )
        assert wrong == 401
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 0

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_undecodable_body_is_kept_and_its_delivery_fails_visibly(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier = await _carrier(setup, CarrierTrackingMode.PUSH_FAT)
        assert await _post_carrier(setup, carrier, b'{"not": "events"}') == 200
        await drain(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM carrier_inbox") == 1
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM outbox_delivery"
                " WHERE consumer = 'carrier_inbox_decode'"
                " AND handled_at IS NULL AND last_error LIKE '%UndecodableInbox%'",
            )
            == 1
        )

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))
