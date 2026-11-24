"""Paying a quote through the buyer's page and the fake PSP
(docs/plan-payments.md §4, §5): a request in, the page, the rows, the
ledger and what the PSP did out. Every merchant, buyer and address here is
made up."""

import asyncio
import html
import uuid
from datetime import timedelta

import psycopg
import pytest

from mor.checkout.flow import QUOTE_VALID_FOR
from mor.checkout.records import SESSION_LIFETIME
from mor.db.connect import connect
from mor.ledger.account import FxPosition, MerchantPayable, PspClearing
from mor.ledger.store import balance
from mor.messaging.facts import PaymentDeclinedV1
from mor.messaging.guard import DuplicateGuard
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.transitions import AttemptState
from mor.psp.port import CaptureMode, StatusMode
from mor.web.staff_layout import money_text
from tests.helpers import count_rows
from tests.payments.support import (
    ACTOR,
    FAT_SALE_KEYED,
    NOW,
    Setup,
    Wiring,
    attempts_of,
    drain,
    pay_on_page,
    release,
    session_state,
    with_setup,
    words,
)


def test_card_payment_completes_the_session_with_one_order_and_its_entry(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        quoted = words((await setup.wired.control.get(setup.page)).text)
        assert "Pay with" in quoted and "Card" in quoted

        paid = await pay_on_page(setup)
        assert paid.status_code == 303, paid.text
        assert await session_state(setup) == "paying"
        waiting = (await setup.wired.control.get(setup.page)).text
        assert "Confirming your payment" in words(waiting)
        assert 'src="/static/js/waiting.js' in waiting
        assert '<noscript><meta http-equiv="refresh"' in waiting

        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        assert attempt.action is not None and attempt.action.kind == "redirect"
        shown = (await setup.wired.control.get(setup.page)).text
        assert "Continue to payment" in words(shown)
        assert attempt.action.url in html.unescape(shown)

        # The buyer approves on the PSP's page and is sent back to ours.
        back = await setup.wired.control.post(
            attempt.action.url, data={"outcome": "paid"}
        )
        assert back.status_code == 303
        assert back.headers["location"] == setup.page
        await drain(setup)

        assert await session_state(setup) == "completed"
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await count_rows(setup.url, "SELECT count(*) FROM order_") == 1
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge"
            )
            == 1
        )
        async with await connect(setup.url) as conn:
            clearing = await balance(
                conn,
                PspClearing(setup.shop.account.id),
                Currency.SGD,
            )
            merchant_balance = await balance(
                conn,
                MerchantPayable(setup.shop.merchant_id),
                Currency.SGD,
            )
        assert clearing == attempt.amount
        # Each PSP event is stored with the currency it named.
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM payment_event WHERE currency = 'SGD'",
            )
            == await count_rows(setup.url, "SELECT count(*) FROM payment_event")
            > 0
        )
        # The order is a fact: it refuses a change.
        async with await connect(setup.url) as conn:
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                await conn.execute("UPDATE order_ SET buyer_token = 'other'")
        # The merchant is owed exactly its price: 2 × 25.00 SGD.
        assert merchant_balance == Money(-5000, Currency.SGD)
        # The paid checkout link leads to the order page (`our_page`).
        landing = await setup.wired.control.get(setup.page)
        assert landing.status_code == 303
        assert landing.headers["location"].startswith("/o/")
        done = words(
            (
                await setup.wired.control.get(
                    f"http://mor{landing.headers['location']}"
                )
            ).text
        )
        assert "Thank you, your order is paid" in done
        assert "Back to Test Shop" in done
        staff = words(
            (
                await setup.wired.control.get(
                    f"http://mor/staff/markets/sessions/cs_{setup.session_id.hex}"
                )
            ).text
        )
        assert "Payment attempts, newest first" in staff
        assert f"card {money_text(attempt.amount)} captured" in staff

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_poll_account_captures_once_after_the_buyer_approves(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.POLL,
        capture_mode=CaptureMode.AUTHORISE_CAPTURE,
        guard=DuplicateGuard.NONE,
    )

    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        setup.clock[0] += timedelta(minutes=1)  # the poll's turn
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await session_state(setup) == "completed"
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge"
            )
            == 1
        )
        # Polling stops at the end: the poll already scheduled finds the
        # attempt captured and schedules no other.
        setup.clock[0] += timedelta(minutes=1)
        await drain(setup)
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM outbox_delivery"
                " WHERE handled_at IS NULL AND consumer::text LIKE 'psp_%'",
            )
            == 0
        )

    asyncio.run(with_setup(database_url, wiring, body))


def test_qr_payment_on_thin_webhooks_shows_the_code_and_completes(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.PUSH_THIN,
        capture_mode=CaptureMode.SALE,
        guard=DuplicateGuard.VENDOR_CONSTRAINT,
    )

    async def body(setup: Setup) -> None:
        await pay_on_page(setup, "qr")
        await drain(setup)
        page = (await setup.wired.control.get(setup.page)).text
        assert '<svg xmlns="http://www.w3.org/2000/svg"' in page
        assert "Scan this code" in words(page)
        assert "This code works until 18-Nov-2026" in words(page)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        await drain(setup)
        assert await session_state(setup) == "completed"

    asyncio.run(with_setup(database_url, wiring, body))


def test_declined_card_lets_the_buyer_pay_again(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "declined")
        await drain(setup)
        assert await session_state(setup) == "quoted"
        page = words((await setup.wired.control.get(setup.page)).text)
        assert "Your payment was declined. You can try again." in page

        await pay_on_page(setup)
        await drain(setup)
        first, second = await attempts_of(setup)
        assert first.state == AttemptState.DECLINED
        await release(setup, second, "paid")
        await drain(setup)
        assert await session_state(setup) == "completed"
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge"
            )
            == 1
        )

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_earlier_attempts_end_met_again_leaves_the_open_one_paying(
    database_url: str,
) -> None:
    """A session's first attempt ended and the buyer is paying again; that
    first end, appended once more (a producer retrying), must not set the
    session back to `quoted` under the open second attempt."""

    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (first,) = await attempts_of(setup)
        await release(setup, first, "declined")
        await drain(setup)
        await pay_on_page(setup)
        await drain(setup)
        assert await session_state(setup) == "paying"

        async with await connect(setup.url) as conn:
            await append_fact(
                conn,
                PaymentDeclinedV1(
                    payment_attempt_id=first.id,
                    checkout_session_id=setup.session_id,
                    reason="declined",
                ),
                ACTOR,
                setup.clock[0],
            )
            await conn.commit()
        await drain(setup)

        assert await session_state(setup) == "paying"
        _, second = await attempts_of(setup)
        await release(setup, second, "paid")
        await drain(setup)
        assert await session_state(setup) == "completed"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_double_pay_makes_one_attempt(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        first, second = await asyncio.gather(pay_on_page(setup), pay_on_page(setup))
        assert first.status_code == 303 and second.status_code == 303
        assert len(await attempts_of(setup)) == 1

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_pay_on_a_lapsed_quote_makes_no_attempt(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        setup.clock[0] = NOW + QUOTE_VALID_FOR
        response = await pay_on_page(setup)
        assert response.status_code == 303
        assert await attempts_of(setup) == []
        page = words((await setup.wired.control.get(setup.page)).text)
        assert "This price is no longer held." in page
        assert "Pay with" not in page

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_cross_site_pay_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await setup.wired.control.post(
            f"{setup.page}/pay",
            data={"option": setup.option, "method": "card"},
            headers={"origin": "https://evil.example"},
        )
        assert response.status_code == 403
        assert await attempts_of(setup) == []

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_paying_another_way_cancels_at_the_psp_first(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup, "qr")
        await drain(setup)
        # Not yet: the session stays paying until the PSP has answered.
        await setup.wired.control.post(
            f"{setup.page}/pay-another-way", headers={"origin": "http://mor"}
        )
        assert await session_state(setup) == "paying"
        await pay_on_page(setup)
        assert len(await attempts_of(setup)) == 1

        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CANCELLED
        assert await session_state(setup) == "quoted"
        await pay_on_page(setup)
        assert len(await attempts_of(setup)) == 2

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_capture_after_paying_another_way_is_honoured(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup, "qr")
        await drain(setup)
        await setup.wired.control.post(
            f"{setup.page}/pay-another-way", headers={"origin": "http://mor"}
        )
        # The buyer's app paid before our cancel reached the PSP.
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await session_state(setup) == "completed"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


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
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        # The key was replayed: the PSP made one payment.
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment"
            )
            == 1
        )

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_lost_create_answer_is_looked_up_on_an_unkeyed_account(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.PUSH_FAT,
        capture_mode=CaptureMode.SALE,
        guard=DuplicateGuard.NONE,
    )

    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={
                "account": "acct",
                "operation": "create",
                "fault": "fail_500_after_work",
            },
        )
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment"
            )
            == 1
        )

    asyncio.run(with_setup(database_url, wiring, body))


def test_a_lost_create_the_psp_never_shows_goes_to_a_person(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.PUSH_FAT,
        capture_mode=CaptureMode.SALE,
        guard=DuplicateGuard.NONE,
    )

    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "create", "fault": "fail_500"},
        )
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        # A 500 may have done the work; the PSP does not show it, which
        # proves nothing. Never sent again on a `none` guard.
        assert attempt.state == AttemptState.NEEDS_PERSON
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment"
            )
            == 0
        )
        page = words((await setup.wired.control.get(setup.page)).text)
        assert "You won't be charged twice" in page

    asyncio.run(with_setup(database_url, wiring, body))


def test_a_session_being_paid_does_not_expire_until_the_payment_ends(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        setup.clock[0] = NOW + SESSION_LIFETIME + timedelta(minutes=1)
        await drain(setup)
        assert await session_state(setup) == "paying"
        page = await setup.wired.control.get(setup.page)
        assert page.status_code == 200  # not sent away to the cart
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "expired")
        await drain(setup)
        assert await session_state(setup) == "expired"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_goods_priced_in_another_currency_convert_through_fx_position(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        await drain(setup)
        async with await connect(setup.url) as conn:
            merchant_balance = await balance(
                conn,
                MerchantPayable(setup.shop.merchant_id),
                Currency.USD,
            )
            usd_side = await balance(conn, FxPosition(), Currency.USD)
            sgd_side = await balance(conn, FxPosition(), Currency.SGD)
        # Exactly the merchant's price, 2 × 18.00 USD, in its own currency.
        assert merchant_balance == Money(-3600, Currency.USD)
        assert usd_side == Money(3600, Currency.USD)
        assert sgd_side.amount_minor < 0  # the buyer's money held for it

    asyncio.run(
        with_setup(database_url, FAT_SALE_KEYED, body, Money(1800, Currency.USD))
    )


def test_the_status_route_changes_when_the_page_would(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        url = f"{setup.page}/status"
        before = (await setup.wired.control.get(url)).json()["status"]
        await pay_on_page(setup)
        after = (await setup.wired.control.get(url)).json()["status"]
        assert before != after
        page = (await setup.wired.control.get(setup.page)).text
        assert f'data-status="{after}"' in page

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_card_declined_at_once_lets_the_buyer_pay_again(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "create", "fault": "decline"},
        )
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.DECLINED
        assert await session_state(setup) == "quoted"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_lost_capture_answer_on_a_keyed_account_captures_once(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.PUSH_FAT,
        capture_mode=CaptureMode.AUTHORISE_CAPTURE,
        guard=DuplicateGuard.VENDOR_CONSTRAINT,
    )

    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={
                "account": "acct",
                "operation": "capture",
                "fault": "fail_500_after_work",
            },
        )
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge"
            )
            == 1
        )
        assert await count_rows(setup.url, "SELECT count(*) FROM order_") == 1

    asyncio.run(with_setup(database_url, wiring, body))


def test_a_lost_capture_answer_on_an_unkeyed_account_goes_to_a_person(
    database_url: str,
) -> None:
    wiring = Wiring(
        status_mode=StatusMode.POLL,
        capture_mode=CaptureMode.AUTHORISE_CAPTURE,
        guard=DuplicateGuard.NONE,
    )

    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        await setup.wired.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": "capture", "fault": "fail_500"},
        )
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        setup.clock[0] += timedelta(minutes=1)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        # The PSP still shows it authorised, which proves nothing: never
        # captured again by itself.
        assert attempt.state == AttemptState.NEEDS_PERSON
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_charge"
            )
            == 0
        )
        assert await session_state(setup) == "paying"

    asyncio.run(with_setup(database_url, wiring, body))


def test_webhooks_twice_late_or_badly_signed_change_nothing_more(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.provider_payment_id is not None
        released = await setup.wired.control.post(
            f"http://mor/_fake/psp/acct/payments/{attempt.provider_payment_id}/release",
            json={"outcome": "paid", "push": "bad_signature"},
        )
        assert released.json()["kind"] == "captured"
        await drain(setup)
        # Refused unsigned: nothing stored, the attempt still waits.
        assert await count_rows(setup.url, "SELECT count(*) FROM psp_inbox") == 0
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED

        # The PSP retries, then sends everything again, newest first.
        for _ in range(2):
            await setup.wired.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
            await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        assert await count_rows(setup.url, "SELECT count(*) FROM payment_event") == 1
        assert await count_rows(setup.url, "SELECT count(*) FROM order_") == 1
        assert await count_rows(setup.url, "SELECT count(*) FROM journal_entry") == 1

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_webhook_for_an_unknown_account_is_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        response = await setup.wired.control.post(
            f"http://mor/webhooks/psp/{uuid.uuid7()}", content=b"{}"
        )
        assert response.status_code == 404

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))
