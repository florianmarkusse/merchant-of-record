"""Paying a quote through the buyer's page and the fake PSP
(docs/plan-payments.md §4, §5): a request in, the page, the rows, the
ledger and what the PSP did out. Every merchant, buyer and address here is
made up."""

import asyncio
import html
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import LiteralString

import httpx2

from mor.checkout.flow import QUOTE_VALID_FOR
from mor.checkout.records import SESSION_LIFETIME
from mor.checkout.sessions import session_by_id
from mor.db.connect import connect
from mor.ledger.account import Account, AccountKind
from mor.ledger.store import balance
from mor.messaging.guard import DuplicateGuard
from mor.messaging.worker import drain_once
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.attempts import Attempt, session_attempts
from mor.payments.transitions import AttemptState
from mor.psp.port import CaptureMode, StatusMode
from mor.web.staff_layout import money_text
from tests.helpers import ENV, Wired, wire
from tests.payments.support import (
    NOW,
    SGD_PRICE,
    Shop,
    Wiring,
    make_shop,
    quoted_session,
)


FAT_SALE_KEYED = Wiring(
    StatusMode.PUSH_FAT, CaptureMode.SALE, DuplicateGuard.VENDOR_CONSTRAINT
)


@dataclass
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


async def _with_setup(
    url: str,
    wiring: Wiring,
    body: Callable[[Setup], Awaitable[None]],
    price: Money = SGD_PRICE,
) -> None:
    clock = [NOW]
    wired = wire(url, lambda: clock[0])
    try:
        shop = await make_shop(url, wired.control, wiring)
        session_id, option = await quoted_session(url, shop.merchant_id, price)
        await body(Setup(url, wired, clock, shop, session_id, option))
    finally:
        await wired.http.aclose()
        await wired.control.aclose()


async def _pay(setup: Setup, method: str = "card") -> httpx2.Response:
    return await setup.wired.control.post(
        f"{setup.page}/pay",
        data={"option": setup.option, "method": method},
        headers={"origin": "http://mor"},
    )


async def _drain(setup: Setup, rounds: int = 20) -> None:
    """Run the worker until idle, moving the clock past any retry's wait."""
    async with await connect(setup.url, autocommit=True) as worker:
        for _ in range(rounds):
            if not await drain_once(worker, setup.wired.http, ENV, setup.clock[0]):
                setup.clock[0] += timedelta(minutes=2)
                if not await drain_once(worker, setup.wired.http, ENV, setup.clock[0]):
                    return


async def _attempts(setup: Setup) -> list[Attempt]:
    async with await connect(setup.url) as conn:
        return await session_attempts(conn, setup.session_id)


async def _session_state(setup: Setup) -> str:
    async with await connect(setup.url) as conn:
        session = await session_by_id(conn, setup.session_id)
    assert session is not None
    return session.state.value


async def _count(url: str, sql: LiteralString) -> int:
    async with await connect(url) as conn:
        cur = await conn.execute(sql)
        row = await cur.fetchone()
    assert row is not None
    return row[0]


async def _release(setup: Setup, attempt: Attempt, outcome: str) -> None:
    assert attempt.provider_payment_id is not None
    response = await setup.wired.control.post(
        f"http://mor/_fake/psp/{setup.shop.account.name}/payments/"
        f"{attempt.provider_payment_id}/release",
        json={"outcome": outcome},
    )
    assert response.status_code == 200, response.text


def _words(page: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def test_card_payment_completes_the_session_with_one_order_and_its_entry(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        quoted = _words((await setup.wired.control.get(setup.page)).text)
        assert "Pay with" in quoted and "Card" in quoted

        paid = await _pay(setup)
        assert paid.status_code == 303, paid.text
        assert await _session_state(setup) == "paying"
        waiting = (await setup.wired.control.get(setup.page)).text
        assert "Confirming your payment" in _words(waiting)
        assert 'src="/static/js/waiting.js' in waiting
        assert '<noscript><meta http-equiv="refresh"' in waiting

        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        assert attempt.action is not None and attempt.action.kind == "redirect"
        shown = (await setup.wired.control.get(setup.page)).text
        assert "Continue to payment" in _words(shown)
        assert attempt.action.url in html.unescape(shown)

        # The buyer approves on the PSP's page and is sent back to ours.
        back = await setup.wired.control.post(
            attempt.action.url, data={"outcome": "paid"}
        )
        assert back.status_code == 303
        assert back.headers["location"] == setup.page
        await _drain(setup)

        assert await _session_state(setup) == "completed"
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await _count(setup.url, "SELECT count(*) FROM order_") == 1
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge")
            == 1
        )
        async with await connect(setup.url) as conn:
            clearing = await balance(
                conn,
                Account(AccountKind.PSP_CLEARING, str(setup.shop.account.id)),
                Currency.SGD,
            )
            merchant = await balance(
                conn,
                Account(AccountKind.MERCHANT_PAYABLE, str(setup.shop.merchant_id)),
                Currency.SGD,
            )
        assert clearing == attempt.amount
        # The merchant is owed exactly its price: 2 × 25.00 SGD.
        assert merchant == Money(-5000, Currency.SGD)
        done = _words((await setup.wired.control.get(setup.page)).text)
        assert "Thank you, your order is paid" in done
        assert "Back to Test Shop" in done
        staff = _words(
            (
                await setup.wired.control.get(
                    f"http://mor/staff/markets/sessions/cs_{setup.session_id.hex}"
                )
            ).text
        )
        assert "Payment attempts, newest first" in staff
        assert f"card {money_text(attempt.amount)} captured" in staff

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_poll_account_captures_once_after_the_buyer_approves(
    database_url: str,
) -> None:
    wiring = Wiring(StatusMode.POLL, CaptureMode.AUTHORISE_CAPTURE, DuplicateGuard.NONE)

    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        setup.clock[0] += timedelta(minutes=1)  # the poll's turn
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await _session_state(setup) == "completed"
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge")
            == 1
        )
        # Polling stops at the end: the poll already scheduled finds the
        # attempt captured and schedules no other.
        setup.clock[0] += timedelta(minutes=1)
        await _drain(setup)
        assert (
            await _count(
                setup.url,
                "SELECT count(*) FROM outbox_delivery"
                " WHERE handled_at IS NULL AND consumer::text LIKE 'psp_%'",
            )
            == 0
        )

    asyncio.run(_with_setup(database_url, wiring, body))


def test_qr_payment_on_thin_webhooks_shows_the_code_and_completes(
    database_url: str,
) -> None:
    wiring = Wiring(
        StatusMode.PUSH_THIN, CaptureMode.SALE, DuplicateGuard.VENDOR_CONSTRAINT
    )

    async def body(setup: Setup) -> None:
        await _pay(setup, "qr")
        await _drain(setup)
        page = (await setup.wired.control.get(setup.page)).text
        assert '<svg xmlns="http://www.w3.org/2000/svg"' in page
        assert "Scan this code" in _words(page)
        assert "This code works until 18-Nov-2026" in _words(page)
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        await _drain(setup)
        assert await _session_state(setup) == "completed"

    asyncio.run(_with_setup(database_url, wiring, body))


def test_declined_card_lets_the_buyer_pay_again(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "declined")
        await _drain(setup)
        assert await _session_state(setup) == "quoted"
        page = _words((await setup.wired.control.get(setup.page)).text)
        assert "Your payment was declined. You can try again." in page

        await _pay(setup)
        await _drain(setup)
        first, second = await _attempts(setup)
        assert first.state == AttemptState.DECLINED
        await _release(setup, second, "paid")
        await _drain(setup)
        assert await _session_state(setup) == "completed"
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge")
            == 1
        )

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_double_pay_makes_one_attempt(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        first, second = await asyncio.gather(_pay(setup), _pay(setup))
        assert first.status_code == 303 and second.status_code == 303
        assert len(await _attempts(setup)) == 1

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_pay_on_a_lapsed_quote_makes_no_attempt(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        setup.clock[0] = NOW + QUOTE_VALID_FOR
        response = await _pay(setup)
        assert response.status_code == 303
        assert await _attempts(setup) == []
        page = _words((await setup.wired.control.get(setup.page)).text)
        assert "This price is no longer held." in page
        assert "Pay with" not in page

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_cross_site_pay_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await setup.wired.control.post(
            f"{setup.page}/pay",
            data={"option": setup.option, "method": "card"},
            headers={"origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert await _attempts(setup) == []

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_paying_another_way_cancels_at_the_psp_first(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup, "qr")
        await _drain(setup)
        # Not yet: the session stays paying until the PSP has answered.
        await setup.wired.control.post(
            f"{setup.page}/pay-another-way", headers={"origin": "http://mor"}
        )
        assert await _session_state(setup) == "paying"
        await _pay(setup)
        assert len(await _attempts(setup)) == 1

        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CANCELLED
        assert await _session_state(setup) == "quoted"
        await _pay(setup)
        assert len(await _attempts(setup)) == 2

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_capture_after_paying_another_way_is_honoured(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup, "qr")
        await _drain(setup)
        await setup.wired.control.post(
            f"{setup.page}/pay-another-way", headers={"origin": "http://mor"}
        )
        # The buyer's app paid before our cancel reached the PSP.
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await _session_state(setup) == "completed"

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_lost_create_answer_is_sent_again_on_a_keyed_account(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={
                "account": "acct",
                "operation": "create",
                "fault": "fail_500_after_work",
            },
        )
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        # The key was replayed: the PSP made one payment.
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment")
            == 1
        )

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_lost_create_answer_is_looked_up_on_an_unkeyed_account(
    database_url: str,
) -> None:
    wiring = Wiring(StatusMode.PUSH_FAT, CaptureMode.SALE, DuplicateGuard.NONE)

    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={
                "account": "acct",
                "operation": "create",
                "fault": "fail_500_after_work",
            },
        )
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment")
            == 1
        )

    asyncio.run(_with_setup(database_url, wiring, body))


def test_a_lost_create_the_psp_never_shows_goes_to_a_person(
    database_url: str,
) -> None:
    wiring = Wiring(StatusMode.PUSH_FAT, CaptureMode.SALE, DuplicateGuard.NONE)

    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "create", "fault": "fail_500"},
        )
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        # A 500 may have done the work; the PSP does not show it, which
        # proves nothing. Never sent again on a `none` guard.
        assert attempt.state == AttemptState.NEEDS_PERSON
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment")
            == 0
        )
        page = _words((await setup.wired.control.get(setup.page)).text)
        assert "You won't be charged twice" in page

    asyncio.run(_with_setup(database_url, wiring, body))


def test_a_session_being_paid_does_not_expire_until_the_payment_ends(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        setup.clock[0] = NOW + SESSION_LIFETIME + timedelta(minutes=1)
        await _drain(setup)
        assert await _session_state(setup) == "paying"
        page = await setup.wired.control.get(setup.page)
        assert page.status_code == 200  # not sent away to the cart
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "expired")
        await _drain(setup)
        assert await _session_state(setup) == "expired"

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_goods_priced_in_another_currency_convert_through_fx_position(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        await _drain(setup)
        async with await connect(setup.url) as conn:
            merchant = await balance(
                conn,
                Account(AccountKind.MERCHANT_PAYABLE, str(setup.shop.merchant_id)),
                Currency.USD,
            )
            usd_side = await balance(
                conn, Account(AccountKind.FX_POSITION), Currency.USD
            )
            sgd_side = await balance(
                conn, Account(AccountKind.FX_POSITION), Currency.SGD
            )
        # Exactly the merchant's price, 2 × 18.00 USD, in its own currency.
        assert merchant == Money(-3600, Currency.USD)
        assert usd_side == Money(3600, Currency.USD)
        assert sgd_side.amount_minor < 0  # the buyer's money held for it

    asyncio.run(
        _with_setup(database_url, FAT_SALE_KEYED, body, Money(1800, Currency.USD))
    )


def test_the_status_route_changes_when_the_page_would(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        url = f"{setup.page}/status"
        before = (await setup.wired.control.get(url)).json()["status"]
        await _pay(setup)
        after = (await setup.wired.control.get(url)).json()["status"]
        assert before != after
        page = (await setup.wired.control.get(setup.page)).text
        assert f'data-status="{after}"' in page

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_card_declined_at_once_lets_the_buyer_pay_again(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "create", "fault": "decline"},
        )
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.DECLINED
        assert await _session_state(setup) == "quoted"

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_lost_capture_answer_on_a_keyed_account_captures_once(
    database_url: str,
) -> None:
    wiring = Wiring(
        StatusMode.PUSH_FAT,
        CaptureMode.AUTHORISE_CAPTURE,
        DuplicateGuard.VENDOR_CONSTRAINT,
    )

    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={
                "account": "acct",
                "operation": "capture",
                "fault": "fail_500_after_work",
            },
        )
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge")
            == 1
        )
        assert await _count(setup.url, "SELECT count(*) FROM order_") == 1

    asyncio.run(_with_setup(database_url, wiring, body))


def test_a_lost_capture_answer_on_an_unkeyed_account_goes_to_a_person(
    database_url: str,
) -> None:
    wiring = Wiring(StatusMode.POLL, CaptureMode.AUTHORISE_CAPTURE, DuplicateGuard.NONE)

    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "capture", "fault": "fail_500"},
        )
        (attempt,) = await _attempts(setup)
        await _release(setup, attempt, "paid")
        setup.clock[0] += timedelta(minutes=1)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        # The PSP still shows it authorised, which proves nothing: never
        # captured again by itself.
        assert attempt.state == AttemptState.NEEDS_PERSON
        assert (
            await _count(setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge")
            == 0
        )
        assert await _session_state(setup) == "paying"

    asyncio.run(_with_setup(database_url, wiring, body))


def test_webhooks_twice_late_or_badly_signed_change_nothing_more(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _pay(setup)
        await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.provider_payment_id is not None
        released = await setup.wired.control.post(
            f"http://mor/_fake/psp/acct/payments/{attempt.provider_payment_id}/release",
            json={"outcome": "paid", "push": "bad_signature"},
        )
        assert released.json()["kind"] == "captured"
        await _drain(setup)
        # Refused unsigned: nothing stored, the attempt still waits.
        assert await _count(setup.url, "SELECT count(*) FROM psp_inbox") == 0
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED

        # The PSP retries, then sends everything again, newest first.
        for _ in range(2):
            await setup.wired.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
            await _drain(setup)
        (attempt,) = await _attempts(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await _count(setup.url, "SELECT count(*) FROM payment_event") == 1
        assert await _count(setup.url, "SELECT count(*) FROM order_") == 1
        assert await _count(setup.url, "SELECT count(*) FROM journal_entry") == 1

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_webhook_for_an_unknown_account_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await setup.wired.control.post(
            f"http://mor/webhooks/psp/{uuid.uuid7()}", content=b"{}"
        )
        assert response.status_code == 404

    asyncio.run(_with_setup(database_url, FAT_SALE_KEYED, body))
