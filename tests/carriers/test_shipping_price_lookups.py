"""Live carrier prices through the worker, against the fake carrier
in-process (docs/plan-quote.md §4, 4c-2): one open request per
combination, a price held while usable by the account's rule, every call
counted, our own card's price stored beside the carrier's."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from mor.audit import Actor, ActorKind
from mor.carriers import registry
from mor.carriers.prices import (
    NoCarrierAccount,
    carrier_account,
    create_carrier_account,
    read_price,
    request_shipping_price,
    set_price_rule,
)
from mor.carriers.records import (
    CarrierProvider,
    FinishedLookup,
    Parcel,
    PriceExpiry,
    PriceHeld,
    PricePart,
    PricePartKind,
    PriceQuestion,
    PriceState,
)
from mor.config import Environment
from mor.db.connect import Connection, connect
from mor.ids import CarrierCode, CountryCode, ServiceCode, ShippingPriceLookupId
from mor.markets.store import Loaded, load_market
from mor.messaging.worker import drain_once
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.provider_accounts import AccountNotAllowed, ProviderAccountKind
from tests.helpers import ENV, VENDOR_TIMEOUT, Wired, count_rows, wire
from tests.quote.fixtures import quote_market


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
LATER = NOW + timedelta(seconds=3)  # past the first retry's backoff
ACTOR = Actor(ActorKind.SYSTEM, "test")
VALID_FOR = timedelta(hours=24)
# 1 kg in a box that weighs less by volume: billed 1 kg at the fake, and at
# our card's 1 kg break.
BOX = Parcel(weight_g=1000, length_mm=200, width_mm=150, height_mm=100)
TO_SG = PriceQuestion(
    carrier=CarrierCode("carrier"),
    service=ServiceCode("exp"),
    origin=CountryCode("VN"),
    destination=CountryCode("SG"),
    parcel=BOX,
)


@dataclass
class Setup:
    url: str
    wired: Wired
    worker: Connection


async def _with_setup(url: str, body: Callable[[Setup], Awaitable[None]]) -> None:
    wired = wire(url, lambda: NOW)
    async with await connect(url, autocommit=True) as worker:
        async with await connect(url) as conn:
            await create_carrier_account(
                conn,
                CarrierCode("carrier"),
                CarrierProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/carrier/acct",
                VENDOR_TIMEOUT,
                VALID_FOR,
                PriceExpiry.CARRIER_FIRST,
            )
            await conn.commit()
        try:
            await body(Setup(url, wired, worker))
        finally:
            await wired.http.aclose()
            await wired.control.aclose()


async def _ask(setup: Setup, now: datetime = NOW) -> ShippingPriceLookupId:
    async with await connect(setup.url) as conn:
        asked = await request_shipping_price(conn, TO_SG, ACTOR, now)
        await conn.commit()
    assert not isinstance(asked, NoCarrierAccount)
    return asked


async def _drain(setup: Setup, now: datetime = NOW) -> None:
    await drain_once(setup.worker, setup.wired.http, ENV, now)


async def _held(setup: Setup) -> PriceHeld:
    async with await connect(setup.url) as conn:
        return await read_price(conn, TO_SG)


async def _latest(setup: Setup) -> FinishedLookup:
    held = await _held(setup)
    assert held.latest is not None
    return held.latest


async def _carrier_calls(setup: Setup) -> int:
    return await count_rows(setup.url, "SELECT count(*) FROM fake_vendor.carrier_call")


async def _facts(setup: Setup, lookup_id: ShippingPriceLookupId) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT message_type::text FROM outbox WHERE aggregate_id = %s"
            " ORDER BY seq",
            (lookup_id,),
        )
        rows = await cur.fetchall()
    types: list[str] = []
    for (message_type,) in rows:
        types.append(message_type)
    return types


async def _fault(setup: Setup, fault: str) -> None:
    response = await setup.wired.control.post(
        "http://mor/_fake/carrier/faults", json={"fault": fault}
    )
    assert response.status_code == 204


async def _carrier_rate(setup: Setup, valid_until: datetime | None) -> None:
    response = await setup.wired.control.post(
        "http://mor/_fake/carrier/rates",
        json={
            "service": "exp",
            "destination": "SG",
            "currency": "SGD",
            "base_minor": 2000,
            "per_kg_minor": 1000,
            "fuel_bp": 5000,
            "surcharge_minor": 0,
            "valid_until": None if valid_until is None else valid_until.isoformat(),
        },
    )
    assert response.status_code == 204


def test_a_price_is_asked_once_while_open_and_stored_with_its_parts(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        first = await _ask(setup)
        assert await _ask(setup) == first  # open: the same request
        held = await _held(setup)
        assert held == PriceHeld(None, first)
        await _drain(setup)
        assert await _ask(setup) == first  # answered and usable

        latest = await _latest(setup)
        assert latest.state == PriceState.ANSWERED and latest.calls == 1
        assert latest.price is not None
        # The fake's default: VND 300,000 + 150,000 per kg, fuel 30%, and
        # a VND 20,000 surcharge.
        assert latest.price.parts == (
            PricePart(PricePartKind.FREIGHT, "freight", 450_000),
            PricePart(PricePartKind.FUEL, "fuel surcharge", 135_000),
            PricePart(PricePartKind.SURCHARGE, "demand surcharge", 20_000),
        )
        assert latest.price.total == Money(605_000, Currency.VND)
        assert latest.usable_until == NOW + VALID_FOR
        assert await _carrier_calls(setup) == 1
        assert await _facts(setup, first) == [
            "shipping_price_needed",
            "shipping_price_found",
        ]

    asyncio.run(_with_setup(database_url, body))


def test_our_cards_price_for_the_same_lane_is_stored_beside_it(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            loaded = await load_market(conn, quote_market(), "test", ACTOR, NOW)
            await conn.commit()
        assert isinstance(loaded, Loaded)
        await _carrier_rate(setup, None)
        await _ask(setup)
        await _drain(setup)
        price = (await _latest(setup)).price
        assert price is not None
        assert price.total == Money(4500, Currency.SGD)  # 3,000 + 1,500 fuel
        assert price.own is not None
        # exp_card at 1 kg: SGD 30.00, demand 1.00, fuel 50% on both.
        assert price.own.load_id == loaded.load_id and price.own.rate_card == "exp_card"
        assert price.own.total == Money(4650, Currency.SGD)

    asyncio.run(_with_setup(database_url, body))


def test_a_price_is_asked_again_once_its_period_ends(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        first = await _ask(setup)
        await _drain(setup)
        expired = NOW + VALID_FOR
        second = await _ask(setup, expired)
        assert second != first
        await _drain(setup, expired)
        assert (await _latest(setup)).id == second
        assert await _carrier_calls(setup) == 2

    asyncio.run(_with_setup(database_url, body))


def test_the_carriers_own_validity_ends_a_price_unless_the_account_says_otherwise(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        carrier_until = NOW + timedelta(hours=2)
        await _carrier_rate(setup, carrier_until)
        await _ask(setup)
        await _drain(setup)
        assert (await _latest(setup)).usable_until == carrier_until

        # Switched: the same stored price is now used for our full period.
        async with await connect(setup.url) as conn:
            await set_price_rule(
                conn, CarrierCode("carrier"), VALID_FOR, PriceExpiry.OWN_PERIOD
            )
            await conn.commit()
        assert (await _latest(setup)).usable_until == NOW + VALID_FOR

    asyncio.run(_with_setup(database_url, body))


def test_a_lost_answer_is_asked_again_and_every_call_counted(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _ask(setup)
        await _fault(setup, "timeout_after_work")
        await _drain(setup)
        assert (await _held(setup)).latest is None

        setup.wired.network.refuse_next = 1
        await _drain(setup, LATER)
        assert (await _held(setup)).latest is None

        await _drain(setup, LATER + timedelta(hours=1))
        latest = await _latest(setup)
        assert latest.state == PriceState.ANSWERED
        # Three calls counted; the refused one never reached the carrier.
        assert latest.calls == 3
        assert await _carrier_calls(setup) == 2

    asyncio.run(_with_setup(database_url, body))


@pytest.mark.parametrize(
    ("fault", "state"),
    [
        ("not_served", PriceState.NOT_SERVED),
        ("undecodable", PriceState.NEEDS_PERSON),
        ("parts_mismatch", PriceState.NEEDS_PERSON),
    ],
)
def test_not_served_or_an_unreadable_answer_is_held_without_a_price(
    database_url: str, fault: str, state: PriceState
) -> None:
    async def body(setup: Setup) -> None:
        lookup_id = await _ask(setup)
        await _fault(setup, fault)
        await _drain(setup)
        latest = await _latest(setup)
        assert latest.state == state and latest.price is None
        assert latest.detail
        facts = await _facts(setup, lookup_id)
        assert facts[-1] == f"shipping_price_{state.value}"
        # Held like a price, so asking again within the period asks nobody.
        assert await _ask(setup) == lookup_id

    asyncio.run(_with_setup(database_url, body))


def test_a_price_in_another_currency_is_stored_as_the_carrier_gave_it(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _ask(setup)
        await _fault(setup, "other_currency")
        await _drain(setup)
        price = (await _latest(setup)).price
        assert price is not None
        assert price.total == Money(2420, Currency.USD)  # 605,000 VND / 250

    asyncio.run(_with_setup(database_url, body))


def test_a_carrier_without_an_account_here_cannot_be_asked(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        other = PriceQuestion(
            carrier=CarrierCode("other"),
            service=ServiceCode("exp"),
            origin=CountryCode("VN"),
            destination=CountryCode("SG"),
            parcel=BOX,
        )
        async with await connect(setup.url) as conn:
            asked = await request_shipping_price(conn, other, ACTOR, NOW)
        assert asked == NoCarrierAccount(CarrierCode("other"))

    asyncio.run(_with_setup(database_url, body))


def test_the_fake_carrier_is_refused_in_prod(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            account = await carrier_account(conn, CarrierCode("carrier"))
        assert account is not None
        with pytest.raises(AccountNotAllowed):
            await registry.quote_rate(
                setup.wired.http, Environment.PROD, account, TO_SG
            )

    asyncio.run(_with_setup(database_url, body))
