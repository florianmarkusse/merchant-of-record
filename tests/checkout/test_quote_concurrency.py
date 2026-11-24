"""The one race in the quote flow, with real concurrent connections
(docs/plan-quote.md §5, 4d-2; conventions: a small suite of real
concurrent workers checks the locks themselves).

A buyer's address step builds while the carrier's price it needs is open,
and records that it waits on it. If the price's completion committed
between that build and the step's commit, its outcome fact would find no
one waiting, and the buyer would wait for ever. `still_open` takes a share
lock on each lookup it waits on while reading its state, so the step sees
the price finish and builds again with it (the foreign key from its wait
row alone would lock the lookup only after the state was read)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import mor.couriers.prices
from mor.audit import Actor, ActorKind
from mor.checkout.addresses import Address
from mor.checkout.flow import Waiting, submit_address
from mor.checkout.records import AfterPayment, Customs, SessionDraft, SoldLine
from mor.checkout.sessions import create_session, session_by_id
from mor.couriers.prices import PriceCall, create_courier_account
from mor.couriers.records import CourierProvider, Parcel, PriceExpiry
from mor.db.connect import connect
from mor.markets.store import load_market
from mor.merchants.store import create_merchant
from mor.messaging.delivery import finish_delivery
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import (
    drain_once,
    find_candidates,
    try_lock_aggregate,
    unlock_aggregate,
)
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind
from tests.helpers import ENV, wire
from tests.quote.fixtures import live_market


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
PARCEL = Parcel(weight_g=1000, length_mm=200, width_mm=150, height_mm=100)
SG = Address(
    name="Tan Mei Ling",
    email="mei@example.test",
    phone_dial_code="+65",
    phone_number="91234567",
    line1="1 Example Road",
    line2=None,
    city="Singapore",
    region=None,
    postcode="123456",
    country="SG",
)
# Long enough for the second buyer's step to reach the lookup it waits on
# and block there.
SETTLE_S = 0.3


def test_a_price_finishing_while_an_address_is_quoted_is_never_missed(
    database_url: str,
) -> None:
    async def body() -> None:
        wired = wire(database_url, lambda: NOW)
        sessions: list[uuid.UUID] = []
        async with await connect(database_url) as conn:
            await load_market(conn, live_market(), "test", ACTOR, NOW)
            merchant = await create_merchant(
                conn, "Test Shop Co.", "Test Shop", "VN", 100, ACTOR, NOW
            )
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/courier/acct",
                timedelta(seconds=2),
                timedelta(hours=24),
                PriceExpiry.CARRIER_FIRST,
            )
            line = SoldLine(
                name="Linen shirt",
                quantity=1,
                unit_price=Money(2500, Currency.SGD),
                category="apparel",
                merchant_sku=None,
                customs=Customs(
                    description="Linen shirt",
                    origin_country="VN",
                    weight_g=300,
                    hs_code="610910",
                ),
            )
            for _ in range(2):
                session = await create_session(
                    conn,
                    merchant.id,
                    SessionDraft(
                        lines=(line,),
                        parcel=PARCEL,
                        merchant_reference=None,
                        return_url="https://s/t",
                        cancel_url="https://s/c",
                        after_payment=AfterPayment.OUR_PAGE,
                    ),
                    ACTOR,
                    NOW,
                )
                sessions.append(session.id)
            # The first buyer's address opens the carrier's price.
            assert isinstance(
                await submit_address(conn, sessions[0], SG, ENV, NOW), Waiting
            )
            await conn.commit()

        worker = await connect(database_url, autocommit=True)
        completer = await connect(database_url)
        buyer = await connect(database_url)
        try:
            # The worker asks the carrier and stores its answer, holding the
            # lookup's row lock: not committed yet.
            [candidate] = [
                found
                for found in await find_candidates(worker, NOW, 50)
                if found.consumer == ConsumerName.SHIPPING_PRICE_ASK
            ]
            assert await try_lock_aggregate(worker, candidate)
            lookup_id = candidate.aggregate_id
            async with worker.transaction():
                prepared = await mor.couriers.prices.prepare_rate(
                    worker, lookup_id, NOW
                )
            assert isinstance(prepared, PriceCall)
            result = await mor.couriers.prices.ask_rate(wired.http, ENV, prepared)
            await mor.couriers.prices.complete_rate(
                completer, lookup_id, prepared, result, NOW
            )
            await finish_delivery(completer, candidate, NOW)

            # The second buyer's step builds while the price still reads as
            # open, then commits only after the price's completion has been
            # announced: the worst order for it.
            stepping = asyncio.create_task(
                submit_address(buyer, sessions[1], SG, ENV, NOW)
            )
            await asyncio.sleep(SETTLE_S)
            await completer.commit()
            await unlock_aggregate(worker, candidate)
            await stepping
            for _ in range(5):
                await drain_once(worker, wired.http, ENV, NOW)
            await buyer.commit()
            for _ in range(5):
                await drain_once(worker, wired.http, ENV, NOW)

            async with await connect(database_url) as conn:
                for session_id in sessions:
                    session = await session_by_id(conn, session_id)
                    assert session is not None
                    assert session.state.value == "quoted", session_id
        finally:
            await buyer.close()
            await completer.close()
            await worker.close()
            await wired.http.aclose()
            await wired.control.aclose()

    asyncio.run(body())
