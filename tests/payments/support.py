"""Shared setup for the payment, order and invoice tests and the payment
fuzzer: a merchant, a session quoted for a made-up Singapore buyer, and a
fake PSP account in the wiring asked for, set up at the fake as a merchant
would on its dashboard; and the steps the page tests take. Every person
and address here is made up."""

import html
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

from mor.audit import Actor, ActorKind
from mor.checkout import addresses
from mor.checkout.addresses import parse_address
from mor.checkout.flow import submit_address
from mor.checkout.quotes import MadeQuote, read_quote
from mor.checkout.records import AfterPayment, Customs, SessionDraft, SoldLine
from mor.checkout.sessions import create_session, session_by_id
from mor.couriers.records import Parcel
from mor.db.connect import connect
from mor.markets.store import load_market
from mor.merchants.records import KybState
from mor.merchants.store import create_merchant, set_kyb_state
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import Failed, drain_once, find_candidates, run_one
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.attempts import Attempt, session_attempts
from mor.provider_accounts import ProviderAccountKind
from mor.psp.accounts import add_route, create_psp_account
from mor.psp.port import (
    CaptureMode,
    PaymentMethod,
    PspAccount,
    PspProvider,
    SettlementMode,
    StatusMode,
)
from tests.helpers import ENV, VENDOR_TIMEOUT, Wired, wire
from tests.quote.fixtures import quote_market


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
PARCEL = Parcel(weight_g=1000, length_mm=200, width_mm=150, height_mm=100)
SECRET = "whsec-test"
SG_ADDRESS = {
    addresses.NAME: "Tan Mei Ling",
    addresses.EMAIL: "mei@example.test",
    addresses.PHONE: "9123 4567",
    addresses.LINE1: "1 Example Road",
    addresses.CITY: "Singapore",
    addresses.POSTCODE: "123456",
    addresses.COUNTRY: "SG",
}


def line(price: Money) -> SoldLine:
    return SoldLine(
        name="Linen shirt",
        quantity=2,
        unit_price=price,
        category="apparel",
        merchant_sku="LIN-01",
        customs=Customs(
            description="Men's linen shirt, woven",
            origin_country="VN",
            weight_g=300,
            hs_code="610910",
        ),
    )


@dataclass(frozen=True, kw_only=True)
class Wiring:
    status_mode: StatusMode
    capture_mode: CaptureMode
    guard: DuplicateGuard
    settlement_mode: SettlementMode = SettlementMode.FILE


@dataclass(frozen=True)
class Shop:
    merchant_id: uuid.UUID
    account: PspAccount


async def make_shop(
    url: str, control: httpx2.AsyncClient, wiring: Wiring, name: str = "acct"
) -> Shop:
    """The merchant, the market, and one PSP account routed by default."""
    async with await connect(url) as conn:
        await load_market(conn, quote_market(), "test", ACTOR, NOW)
        seller = await create_merchant(
            conn, "Test Shop Co.", "Test Shop", "VN", 100, ACTOR, NOW
        )
        await set_kyb_state(conn, seller.id, KybState.APPROVED, ACTOR, NOW)
        account = await create_psp_account(
            conn,
            name,
            PspProvider.FAKE,
            ProviderAccountKind.FAKE,
            f"http://mor/_fake/psp/{name}",
            VENDOR_TIMEOUT,
            wiring.status_mode,
            wiring.capture_mode,
            wiring.guard,
            (PaymentMethod.CARD, PaymentMethod.QR),
            SECRET,
            wiring.settlement_mode,
            NOW,
        )
        await add_route(conn, account.id, None, None, NOW, ACTOR, NOW)
        await conn.commit()
    pushes = wiring.status_mode != StatusMode.POLL
    response = await control.post(
        f"http://mor/_fake/psp/{name}/setup",
        json={
            "capture": wiring.capture_mode.value,
            "keyed": wiring.guard == DuplicateGuard.VENDOR_CONSTRAINT,
            "webhook_url": f"http://mor/webhooks/psp/{account.id}" if pushes else None,
            "webhook_secret": SECRET if pushes else None,
            "webhook_payload": (
                ("thin" if wiring.status_mode == StatusMode.PUSH_THIN else "full")
                if pushes
                else None
            ),
        },
    )
    assert response.status_code == 204, response.text
    return Shop(seller.id, account)


async def quoted_session(
    url: str,
    merchant_id: uuid.UUID,
    price: Money,
    now: datetime = NOW,
    after_payment: AfterPayment = AfterPayment.OUR_PAGE,
) -> tuple[uuid.UUID, str]:
    """A session quoted for the Singapore address: its id and the
    cheapest option's key."""
    async with await connect(url) as conn:
        session = await create_session(
            conn,
            merchant_id,
            SessionDraft(
                lines=(line(price),),
                parcel=PARCEL,
                merchant_reference="order-1",
                return_url="https://shop.example/thanks",
                cancel_url="https://shop.example/cart",
                after_payment=after_payment,
            ),
            ACTOR,
            now,
        )
        address = parse_address(SG_ADDRESS, frozenset({"SG"}))
        assert not isinstance(address, dict), address
        await submit_address(conn, session.id, address, ENV, now)
        await conn.commit()
        quoted = await session_by_id(conn, session.id)
        assert quoted is not None and quoted.quote_id is not None
        quote = await read_quote(conn, quoted.quote_id)
    assert isinstance(quote, MadeQuote), quote
    return session.id, quote.options[0].key


SGD_PRICE = Money(2500, Currency.SGD)


FAT_SALE_KEYED = Wiring(
    status_mode=StatusMode.PUSH_FAT,
    capture_mode=CaptureMode.SALE,
    guard=DuplicateGuard.VENDOR_CONSTRAINT,
)


@dataclass(kw_only=True)
class Setup:
    url: str
    wired: Wired
    clock: list[datetime]
    shop: Shop
    session_id: uuid.UUID
    option: str

    @property
    def page(self) -> str:
        return f"http://mor/c/cs_{self.session_id.hex}"


async def with_setup(
    url: str,
    wiring: Wiring,
    body: Callable[[Setup], Awaitable[None]],
    price: Money = SGD_PRICE,
    after_payment: AfterPayment = AfterPayment.OUR_PAGE,
) -> None:
    clock = [NOW]
    wired = wire(url, lambda: clock[0])
    try:
        shop = await make_shop(url, wired.control, wiring)
        session_id, option = await quoted_session(
            url, shop.merchant_id, price, NOW, after_payment
        )
        await body(
            Setup(
                url=url,
                wired=wired,
                clock=clock,
                shop=shop,
                session_id=session_id,
                option=option,
            )
        )
    finally:
        await wired.http.aclose()
        await wired.control.aclose()


async def pay_on_page(setup: Setup, method: str = "card") -> httpx2.Response:
    return await setup.wired.control.post(
        f"{setup.page}/pay",
        data={"option": setup.option, "method": method},
        headers={"origin": "http://mor"},
    )


async def drain(setup: Setup, rounds: int = 20) -> None:
    """Run the worker until idle, moving the clock past any retry's wait."""
    async with await connect(setup.url, autocommit=True) as worker:
        for _ in range(rounds):
            if not await drain_once(worker, setup.wired.http, ENV, setup.clock[0]):
                setup.clock[0] += timedelta(minutes=2)
                if not await drain_once(worker, setup.wired.http, ENV, setup.clock[0]):
                    return


async def attempts_of(setup: Setup) -> list[Attempt]:
    async with await connect(setup.url) as conn:
        return await session_attempts(conn, setup.session_id)


async def session_state(setup: Setup) -> str:
    async with await connect(setup.url) as conn:
        session = await session_by_id(conn, setup.session_id)
    assert session is not None
    return session.state.value


async def release(setup: Setup, attempt: Attempt, outcome: str) -> None:
    assert attempt.provider_payment_id is not None
    response = await setup.wired.control.post(
        f"http://mor/_fake/psp/{setup.shop.account.name}/payments/"
        f"{attempt.provider_payment_id}/release",
        json={"outcome": outcome},
    )
    assert response.status_code == 200, response.text


def words(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


async def approve_at_the_psp(setup: Setup) -> None:
    """The buyer pays by card on the PSP's page and comes back. Invoices
    already due are held back: the caller drains."""
    paid = await pay_on_page(setup)
    assert paid.status_code == 303, paid.text
    await drain_all_but_invoices(setup)
    attempt = (await attempts_of(setup))[-1]
    assert attempt.action is not None
    back = await setup.wired.control.post(attempt.action.url, data={"outcome": "paid"})
    assert back.status_code == 303


async def drain_all_but_invoices(setup: Setup) -> None:
    """The worker, holding back the invoice: the moments between payment
    and invoice, made to last."""
    async with await connect(setup.url, autocommit=True) as worker:
        for _ in range(50):
            ran = False
            for candidate in await find_candidates(worker, setup.clock[0], 50):
                if candidate.consumer == ConsumerName.INVOICE_ISSUE:
                    continue
                result = await run_one(
                    worker, setup.wired.http, ENV, candidate, setup.clock[0]
                )
                assert not isinstance(result, Failed), result
                ran = True
            if not ran:
                return
