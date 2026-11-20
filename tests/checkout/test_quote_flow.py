"""The quote flow through the buyer's page (docs/plan-quote.md §5, 4d-2):
an address posted, the page and the rows out; waiting on a carrier's
price while the worker fetches it; refusals, expiry, refreshing a lapsed
quote, and the staff and API views. Every merchant, buyer and address
here is made up."""

import asyncio
import html
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import LiteralString

import httpx2

from mor.audit import Actor, ActorKind
from mor.checkout import addresses as f
from mor.checkout.flow import QUOTE_VALID_FOR
from mor.checkout.quotes import MadeQuote, read_quote, stored_from
from mor.checkout.records import (
    MAX_ADDRESSES,
    SESSION_LIFETIME,
    AfterPayment,
    Customs,
    SessionDraft,
    SoldLine,
)
from mor.checkout.sessions import create_session, session_by_id
from mor.couriers.prices import create_courier_account
from mor.couriers.records import CourierProvider, Parcel, PriceExpiry
from mor.db.connect import connect
from mor.markets.records import MarketSet
from mor.markets.store import load_market
from mor.merchants.records import KeyEnv, KybState
from mor.merchants.store import create_api_key, create_merchant, set_kyb_state
from mor.messaging.worker import drain_once
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import Quote, QuoteLine, QuoteRequest, build_quote
from tests.helpers import ENV, VENDOR_TIMEOUT, Wired, wire
from tests.quote.fixtures import live_market, quote_market


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
PARCEL = Parcel(1000, 200, 150, 100)
LINE = SoldLine(
    "Linen shirt",
    2,
    Money(2500, Currency.SGD),
    "apparel",
    "LIN-01",
    Customs("Men's linen shirt, woven", "VN", 300, "610910"),
)
SG_ADDRESS = {
    f.NAME: "Tan Mei Ling",
    f.EMAIL: "mei@example.test",
    f.PHONE: "9123 4567",
    f.LINE1: "1 Example Road",
    f.CITY: "Singapore",
    f.POSTCODE: "123456",
    f.COUNTRY: "SG",
}
TH_ADDRESS = {
    f.NAME: "Somchai Example",
    f.EMAIL: "somchai@example.test",
    f.PHONE: "081 234 5678",
    f.LINE1: "99/1 Soi Example",
    f.CITY: "Khlong Toei",
    f.REGION: "Bangkok",
    f.POSTCODE: "10110",
    f.COUNTRY: "TH",
}


@dataclass
class Setup:
    url: str
    wired: Wired
    clock: list[datetime]
    session_id: uuid.UUID
    key: str

    @property
    def page(self) -> str:
        return f"http://mor/c/cs_{self.session_id.hex}"


async def _with_setup(
    url: str,
    body: Callable[[Setup], Awaitable[None]],
    market: MarketSet | None = None,
    line: SoldLine = LINE,
) -> None:
    clock = [NOW]
    wired = wire(url, lambda: clock[0])
    async with await connect(url) as conn:
        await load_market(conn, market or quote_market(), "test", ACTOR, NOW)
        merchant = await create_merchant(
            conn, "Test Shop Co.", "Test Shop", "VN", 100, ACTOR, NOW
        )
        await set_kyb_state(conn, merchant.id, KybState.APPROVED, ACTOR, NOW)
        key = await create_api_key(conn, merchant.id, KeyEnv.TEST, NOW)
        session = await create_session(
            conn,
            merchant.id,
            SessionDraft(
                (line,),
                PARCEL,
                "order-1",
                "https://shop.example/thanks",
                "https://shop.example/cart",
                AfterPayment.OUR_PAGE,
            ),
            ACTOR,
            NOW,
        )
        await create_courier_account(
            conn,
            "carrier",
            CourierProvider.FAKE,
            "http://mor/_fake/courier/acct",
            VENDOR_TIMEOUT,
            timedelta(hours=24),
            PriceExpiry.CARRIER_FIRST,
        )
        await conn.commit()
    try:
        await body(Setup(url, wired, clock, session.id, key.text))
    finally:
        await wired.http.aclose()
        await wired.control.aclose()


async def _post(setup: Setup, path: str, data: dict[str, str]) -> httpx2.Response:
    return await setup.wired.control.post(
        f"{setup.page}{path}", data=data, headers={"origin": "http://mor"}
    )


async def _drain(setup: Setup) -> None:
    async with await connect(setup.url, autocommit=True) as worker:
        for _ in range(10):
            if not await drain_once(worker, setup.wired.http, ENV, setup.clock[0]):
                return


async def _count(url: str, sql: LiteralString) -> int:
    async with await connect(url) as conn:
        cur = await conn.execute(sql)
        row = await cur.fetchone()
    assert row is not None
    return row[0]


def _words(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def test_an_address_gets_a_stored_quote_and_the_page_shows_four_lines(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        form = await setup.wired.control.get(setup.page)
        assert form.status_code == 200
        words = _words(form.text)
        assert "Test Shop" in words and "2 × Linen shirt" in words
        assert "Singapore" in words and "Thailand" in words
        assert "Sold and invoiced by" in words
        # `no-referrer` made browsers send `Origin: null` on this page's own
        # POST, which the same-origin check refused (found on the sandbox,
        # 19-Nov-2026); `same-origin` still keeps the link from other sites.
        assert '<meta name="referrer" content="same-origin">' in form.text

        posted = await _post(setup, "/address", SG_ADDRESS)
        assert posted.status_code == 303, posted.text
        assert posted.headers["location"] == f"/c/cs_{setup.session_id.hex}"

        async with await connect(setup.url) as conn:
            session = await session_by_id(conn, setup.session_id)
            assert session is not None and session.state.value == "quoted"
            assert session.quote_id is not None and session.address_id is not None
            stored = await read_quote(conn, session.quote_id)
        assert isinstance(stored, MadeQuote)
        assert stored.valid_until == NOW + QUOTE_VALID_FOR
        # What was stored is what the builder made, read back exactly.
        built = build_quote(
            quote_market(),
            QuoteRequest(
                "SG",
                (QuoteLine("apparel", "610910", "VN", 2, Money(2500, Currency.SGD)),),
                PARCEL,
                NOW.date(),
                NOW,
            ),
            {},
            {},
        )
        assert isinstance(built, Quote)
        assert stored == stored_from(
            built,
            stored.id,
            setup.session_id,
            session.address_id,
            stored.load_id,
            NOW,
            NOW + QUOTE_VALID_FOR,
            stored.inputs,
        )

        page = _words((await setup.wired.control.get(setup.page)).text)
        for label in ("Items", "Shipping", "Duties and taxes", "Total"):
            assert label in page
        assert "This price is held until 18-Nov-2026 10:00 UTC" in page
        # No payment routing in this setup: the page says so instead of a button.
        assert "Payment isn't available for this country yet" in page

        api = await setup.wired.control.get(
            f"http://mor/v1/checkout_sessions/cs_{setup.session_id.hex}",
            headers={"authorization": f"Bearer {setup.key}"},
        )
        session_json = api.json()
        assert session_json["state"] == "quoted"
        assert session_json["ship_to_country"] == "SG"
        assert session_json["quote"]["state"] == "made"
        assert session_json["quote"]["valid_until"] == "2026-11-18T10:00:00Z"
        assert "line1" not in api.text  # the address itself is not returned

    asyncio.run(_with_setup(database_url, body))


def test_a_bad_address_is_shown_back_with_its_errors_and_nothing_stored(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bad = SG_ADDRESS | {f.POSTCODE: "12", f.EMAIL: ""}
        response = await _post(setup, "/address", bad)
        assert response.status_code == 400
        words = _words(response.text)
        assert "This isn't a postcode for that country." in words
        assert "Please fill this in." in words
        assert 'value="Tan Mei Ling"' in response.text  # kept for the buyer
        assert await _count(setup.url, "SELECT count(*) FROM checkout_address") == 0

    asyncio.run(_with_setup(database_url, body))


def test_a_cross_site_post_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await setup.wired.control.post(
            f"{setup.page}/address",
            data=SG_ADDRESS,
            headers={"origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert await _count(setup.url, "SELECT count(*) FROM checkout_address") == 0

    asyncio.run(_with_setup(database_url, body))


def test_a_refused_quote_says_why_in_buyer_words(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await _post(setup, "/address", TH_ADDRESS)
        assert response.status_code == 303
        page = _words((await setup.wired.control.get(setup.page)).text)
        assert "This order can't be priced for that country yet." in page
        assert "Where should we send it?" in page
        assert (
            await _count(
                setup.url, "SELECT count(*) FROM quote WHERE state = 'refused'"
            )
            == 1
        )

    no_hs = SoldLine(
        LINE.name,
        LINE.quantity,
        LINE.unit_price,
        LINE.category,
        LINE.merchant_sku,
        Customs("Men's linen shirt, woven", "VN", 300, None),
    )
    market = quote_market(duty_sources="TH,vendor,acme,\n")
    asyncio.run(_with_setup(database_url, body, market, no_hs))


def test_the_page_waits_while_the_carriers_price_is_fetched(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        response = await _post(setup, "/address", SG_ADDRESS)
        assert response.status_code == 303
        waiting = (await setup.wired.control.get(setup.page)).text
        assert 'http-equiv="refresh"' in waiting
        assert "Working out shipping, duties and taxes" in _words(waiting)
        # A double submit of the same address asks nothing more.
        await _post(setup, "/address", SG_ADDRESS)
        lookups = "SELECT count(*) FROM shipping_price_lookup"
        assert await _count(setup.url, lookups) == 1

        await _drain(setup)
        async with await connect(setup.url) as conn:
            session = await session_by_id(conn, setup.session_id)
            assert session is not None and session.state.value == "quoted"
            assert session.quote_id is not None
            stored = await read_quote(conn, session.quote_id)
        assert isinstance(stored, MadeQuote)
        assert len(stored.inputs.price_lookups) == 1
        keys = [option.key for option in stored.options]
        assert "live:exp" in keys
        page = (await setup.wired.control.get(setup.page)).text
        assert 'http-equiv="refresh"' not in page

    asyncio.run(_with_setup(database_url, body, live_market()))


def test_a_lapsed_quote_can_be_priced_again(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _post(setup, "/address", SG_ADDRESS)
        refused = await _post(setup, "/refresh", {})
        assert refused.status_code == 303
        assert await _count(setup.url, "SELECT count(*) FROM quote") == 1

        setup.clock[0] = NOW + QUOTE_VALID_FOR
        lapsed = _words((await setup.wired.control.get(setup.page)).text)
        assert "This price is no longer held." in lapsed
        assert "Get the current price" in lapsed
        await _post(setup, "/refresh", {})
        assert await _count(setup.url, "SELECT count(*) FROM quote") == 2
        held = _words((await setup.wired.control.get(setup.page)).text)
        assert "This price is held until 18-Nov-2026 10:30 UTC" in held

    asyncio.run(_with_setup(database_url, body))


def test_a_session_takes_at_most_its_number_of_addresses(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        for _ in range(MAX_ADDRESSES):
            response = await _post(setup, "/address", SG_ADDRESS)
            assert response.status_code == 303
        over = await _post(setup, "/address", SG_ADDRESS)
        assert over.status_code == 429
        assert "too many addresses" in _words(over.text)
        count = await _count(setup.url, "SELECT count(*) FROM checkout_address")
        assert count == MAX_ADDRESSES

    asyncio.run(_with_setup(database_url, body))


def test_an_expired_session_sends_the_buyer_back_to_the_shop(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        setup.clock[0] = NOW + SESSION_LIFETIME
        # Shown as over at once; the worker's step then records it.
        page = await setup.wired.control.get(setup.page)
        assert page.status_code == 303
        assert page.headers["location"] == "https://shop.example/cart"
        posted = await _post(setup, "/address", SG_ADDRESS)
        assert posted.status_code == 303
        assert await _count(setup.url, "SELECT count(*) FROM checkout_address") == 0
        await _drain(setup)
        async with await connect(setup.url) as conn:
            session = await session_by_id(conn, setup.session_id)
        assert session is not None and session.state.value == "expired"

    asyncio.run(_with_setup(database_url, body))


def test_the_staff_page_lists_the_session_and_its_quote(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _post(setup, "/address", SG_ADDRESS)
        listing = _words(
            (await setup.wired.control.get("http://mor/staff/markets/sessions")).text
        )
        assert f"cs_{setup.session_id.hex}" in listing and "quoted" in listing
        detail = await setup.wired.control.get(
            f"http://mor/staff/markets/sessions/cs_{setup.session_id.hex}"
        )
        words = _words(detail.text)
        assert "for SG" in words and "Made 18-Nov-2026 09:30 UTC" in words
        assert "Tan Mei Ling" not in words  # the address is not shown

    asyncio.run(_with_setup(database_url, body))
