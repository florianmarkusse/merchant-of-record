"""Booking a paid order's label (docs/plan-shipments.md §6): the shipment
with its customs lines, the label under each duplicate guard after a lost
answer, refusals, a person booking again, and the label through the
orders API. A payment in, the rows, the fake carrier's labels and the
API's answer out. Every merchant, buyer and address here is made up."""

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from psycopg.rows import class_row

from mor.carriers.accounts import create_carrier_account
from mor.carriers.records import CarrierProvider, PriceExpiry
from mor.checkout import addresses
from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.ids import CarrierCode, CountryCode
from mor.merchants.records import KeyEnv
from mor.merchants.ship_from import set_ship_from
from mor.merchants.store import create_api_key, create_merchant
from mor.messaging.guard import DuplicateGuard
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.orders.store import order_by_session
from mor.provider_accounts import ProviderAccountKind
from mor.public_ids import order_public_id, shipment_public_id
from mor.seed import carrier_flavour
from mor.shipments.records import (
    BookingState,
    DocumentKind,
    Shipment,
    ShipmentFailure,
)
from mor.shipments.store import customs_lines, merchant_document, order_shipment
from tests.helpers import VENDOR_TIMEOUT, count_rows
from tests.payments.support import (
    ACTOR,
    FAT_SALE_KEYED,
    NOW,
    SG_ADDRESS,
    Setup,
    approve_at_the_psp,
    drain,
    with_setup,
    words,
)
from tests.quote.fixtures import WAREHOUSE


CARRIER = CarrierCode("carrier")  # every service of the quote set's


async def _carrier_account(setup: Setup, guard: DuplicateGuard) -> None:
    async with await connect(setup.url) as conn:
        await create_carrier_account(
            conn,
            CARRIER,
            CarrierProvider.FAKE,
            ProviderAccountKind.FAKE,
            f"http://mor/_fake/carrier/{carrier_flavour(guard)}/acct",
            VENDOR_TIMEOUT,
            timedelta(hours=24),
            PriceExpiry.CARRIER_FIRST,
            book_guard=guard,
        )
        await conn.commit()


async def _fault(setup: Setup, fault: str, operation: str = "book") -> None:
    response = await setup.wired.control.post(
        "http://mor/_fake/carrier/faults",
        json={"fault": fault, "operation": operation},
    )
    assert response.status_code == 204, response.text


async def _paid(setup: Setup) -> None:
    await approve_at_the_psp(setup)
    await drain(setup)


async def _shipment(setup: Setup) -> Shipment:
    async with await connect(setup.url) as conn:
        order = await order_by_session(conn, setup.session_id)
        assert order is not None
        shipment = await order_shipment(conn, order.id)
    assert shipment is not None
    return shipment


@dataclass(frozen=True)
class _FakeLabel:
    tracking_number: str
    state: str


async def _labels_at_the_carrier(setup: Setup, shipment: Shipment) -> list[_FakeLabel]:
    async with await connect(setup.url) as conn:
        async with conn.cursor(row_factory=class_row(_FakeLabel)) as cur:
            await cur.execute(
                """
                SELECT tracking_number, state::text AS state
                FROM fake_vendor.carrier_shipment
                WHERE reference = %s ORDER BY tracking_number
                """,
                (str(shipment.id),),
            )
            return await cur.fetchall()


async def _book_requests(setup: Setup, shipment: Shipment) -> int:
    async with await connect(setup.url) as conn:
        return await one_value(
            conn,
            """
            SELECT count(*)::int FROM carrier_request
            WHERE shipment_id = %s AND operation = 'book'
            """,
            (shipment.id,),
            int,
        )


async def _book_again(setup: Setup, shipment: Shipment) -> str:
    """A person presses Book again: the page's note."""
    response = await setup.wired.control.post(
        f"http://mor/staff/shipments/{shipment_public_id(shipment.id)}/book",
        headers={"origin": "http://mor"},
    )
    assert response.status_code == 303, response.text
    page = await setup.wired.control.get(response.headers["location"])
    return words(page.text)


def test_a_paid_order_gets_one_label_with_its_customs_and_documents(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _carrier_account(setup, DuplicateGuard.VENDOR_CONSTRAINT)
        await _paid(setup)
        shipment = await _shipment(setup)
        assert shipment.booking_state == BookingState.LABELLED
        assert shipment.tracking_number is not None
        assert shipment.export_declaration_no is not None
        labels = await _labels_at_the_carrier(setup, shipment)
        assert labels == [_FakeLabel(shipment.tracking_number, "live")]
        async with await connect(setup.url) as conn:
            customs = await customs_lines(conn, shipment.id)
            label = await merchant_document(
                conn, setup.shop.merchant_id, shipment.order_id, DocumentKind.LABEL
            )
            commercial = await merchant_document(
                conn,
                setup.shop.merchant_id,
                shipment.order_id,
                DocumentKind.COMMERCIAL_INVOICE,
            )
        # The sold line, two shirts at 25.00 SGD, under our code.
        assert len(customs) == 1
        assert customs[0].hs_code == "610910"
        assert customs[0].quantity == 2
        assert customs[0].value == Money(5000, Currency.SGD)
        assert label is not None and label.body.startswith(b"%PDF-")
        assert commercial is not None and commercial.body.startswith(b"%PDF-")

        # Redelivering order_paid makes no second shipment.
        assert await count_rows(setup.url, "SELECT 1 FROM shipment") == 1

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


@pytest.mark.parametrize("guard", list(DuplicateGuard))
def test_a_lost_booking_answer_leaves_one_live_label_under_each_guard(
    database_url: str, guard: DuplicateGuard
) -> None:
    async def body(setup: Setup) -> None:
        await _carrier_account(setup, guard)
        # The carrier books, then the answer never comes.
        await _fault(setup, "timeout_after_work")
        await _paid(setup)
        shipment = await _shipment(setup)
        assert shipment.booking_state == BookingState.LABELLED
        labels = await _labels_at_the_carrier(setup, shipment)
        live: list[str] = []
        for label in labels:
            if label.state == "live":
                live.append(label.tracking_number)
        assert live == [shipment.tracking_number]
        match guard:
            case DuplicateGuard.VENDOR_CONSTRAINT | DuplicateGuard.NONE:
                # Replayed by its key, or looked up: one label made.
                assert len(labels) == 1
            case DuplicateGuard.COMPENSABLE:
                # Sent again: a second label, cancelled.
                assert len(labels) == 2

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_booking_lost_on_a_carrier_without_keys_waits_for_a_person(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _carrier_account(setup, DuplicateGuard.NONE)
        # A 500 before the work: nothing booked, and we cannot know that.
        await _fault(setup, "fail_500")
        await _paid(setup)
        stuck = await _shipment(setup)
        assert stuck.booking_state == BookingState.NEEDS_PERSON
        assert await _labels_at_the_carrier(setup, stuck) == []

        note = await _book_again(setup, stuck)
        assert f"{shipment_public_id(stuck.id)} is booked again" in note
        await drain(setup)
        booked = await _shipment(setup)
        assert booked.booking_state == BookingState.LABELLED
        assert len(await _labels_at_the_carrier(setup, booked)) == 1
        assert await _book_requests(setup, booked) == 2

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_refused_address_fails_the_label_and_booking_again_asks_again(
    database_url: str,
) -> None:
    buyer = SG_ADDRESS | {addresses.LINE1: "REFUSE 1 Example Road"}

    async def body(setup: Setup) -> None:
        await _carrier_account(setup, DuplicateGuard.VENDOR_CONSTRAINT)
        await _paid(setup)
        refused = await _shipment(setup)
        assert refused.booking_state == BookingState.LABEL_FAILED
        assert refused.failure == ShipmentFailure.ADDRESS_REFUSED

        await _book_again(setup, refused)
        await drain(setup)
        again = await _shipment(setup)
        assert again.booking_state == BookingState.LABEL_FAILED
        assert await _book_requests(setup, again) == 2
        assert await _labels_at_the_carrier(setup, again) == []

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body, buyer=buyer))


def test_a_ship_from_moved_after_the_quote_fails_without_asking_the_carrier(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _carrier_account(setup, DuplicateGuard.VENDOR_CONSTRAINT)
        # Quoted from VN; by the time the buyer pays, the merchant ships
        # from TH, where no service of ours leaves from.
        async with await connect(setup.url) as conn:
            moved = replace(WAREHOUSE, country=CountryCode("TH"))
            await set_ship_from(conn, setup.shop.merchant_id, moved, ACTOR, NOW)
            await conn.commit()
        await _paid(setup)
        shipment = await _shipment(setup)
        assert shipment.booking_state == BookingState.LABEL_FAILED
        assert shipment.failure == ShipmentFailure.SHIP_FROM_ELSEWHERE
        assert await _book_requests(setup, shipment) == 0

        note = await _book_again(setup, shipment)
        assert "not booked again" in note and "by hand" in note
        assert await _book_requests(setup, shipment) == 0

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_carrier_without_an_account_is_booked_once_it_has_one(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _paid(setup)
        waiting = await _shipment(setup)
        assert waiting.booking_state == BookingState.LABEL_FAILED
        assert waiting.failure == ShipmentFailure.NO_CARRIER_ACCOUNT
        note = await _book_again(setup, waiting)
        assert "has no account here yet" in note

        await _carrier_account(setup, DuplicateGuard.COMPENSABLE)
        await _book_again(setup, waiting)
        await drain(setup)
        booked = await _shipment(setup)
        assert booked.booking_state == BookingState.LABELLED
        assert booked.carrier_account_id is not None

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_cross_site_book_again_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _paid(setup)
        shipment = await _shipment(setup)
        response = await setup.wired.control.post(
            f"http://mor/staff/shipments/{shipment_public_id(shipment.id)}/book",
            headers={"origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert await _book_requests(setup, shipment) == 0

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_the_merchant_gets_its_label_and_no_other_merchant_does(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _carrier_account(setup, DuplicateGuard.VENDOR_CONSTRAINT)
        await _paid(setup)
        shipment = await _shipment(setup)
        public = order_public_id(shipment.order_id)
        async with await connect(setup.url) as conn:
            ours = await create_api_key(conn, setup.shop.merchant_id, KeyEnv.TEST, NOW)
            other = await create_merchant(
                conn, "Other Shop Co.", "Other Shop", CountryCode("TH"), 100, ACTOR, NOW
            )
            theirs = await create_api_key(conn, other.id, KeyEnv.TEST, NOW)
            await conn.commit()
        auth = {"authorization": f"Bearer {ours.text}"}

        order = await setup.wired.control.get(
            f"http://mor/v1/orders/{public}", headers=auth
        )
        assert order.status_code == 200, order.text
        assert order.json()["shipment"] == {
            "id": shipment_public_id(shipment.id),
            "booking": "labelled",
            "tracking": "none",
            "carrier": "carrier",
            "service": shipment.service,
            "tracking_number": shipment.tracking_number,
            "label_url": f"http://mor/v1/orders/{public}/label",
        }
        label = await setup.wired.control.get(
            f"http://mor/v1/orders/{public}/label", headers=auth
        )
        assert label.status_code == 200
        assert label.headers["content-type"] == "application/pdf"
        assert label.content.startswith(b"%PDF-")

        foreign = await setup.wired.control.get(
            f"http://mor/v1/orders/{public}/label",
            headers={"authorization": f"Bearer {theirs.text}"},
        )
        assert foreign.status_code == 404

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_shipment_with_no_account_and_a_moved_ship_from_waits_for_the_account(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            moved = replace(WAREHOUSE, country=CountryCode("TH"))
            await set_ship_from(conn, setup.shop.merchant_id, moved, ACTOR, NOW)
            await conn.commit()
        await _paid(setup)
        shipment = await _shipment(setup)
        assert shipment.booking_state == BookingState.LABEL_FAILED
        assert shipment.failure == ShipmentFailure.NO_CARRIER_ACCOUNT

        # Once there is an account, booking again meets the ship-from.
        await _carrier_account(setup, DuplicateGuard.VENDOR_CONSTRAINT)
        note = await _book_again(setup, shipment)
        assert "by hand" in note

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))
