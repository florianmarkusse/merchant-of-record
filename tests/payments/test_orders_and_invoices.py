"""After the payment (docs/plan-payments.md §6): the invoice with its
gapless number, the buyer's order page, where a paid buyer lands, the KYB
gate, and the orders and merchant API. A request in, the page, the rows
and the API's answer out. Every merchant, buyer and address here is made
up."""

import asyncio
import uuid

from mor.audit import ActorKind
from mor.checkout.records import SESSION_LIFETIME, AfterPayment
from mor.db.connect import connect
from mor.ids import CountryCode
from mor.merchants.records import KeyEnv, KybState
from mor.merchants.store import create_api_key, create_merchant, set_kyb_state
from mor.messaging.consumers import run_local
from mor.messaging.facts import OrderPaidV1
from mor.messaging.outcome import Stale
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import (
    find_candidates,
    handle_delivery,
)
from mor.orders.store import order_by_session
from mor.web.staff_layout import money_text
from tests.helpers import ENV, count_rows, crash, local_consumer
from tests.payments.support import (
    ACTOR,
    FAT_SALE_KEYED,
    NOW,
    SGD_PRICE,
    Setup,
    approve_at_the_psp,
    attempts_of,
    drain,
    drain_all_but_invoices,
    pay_on_page,
    quoted_session,
    with_setup,
    words,
)


async def _order_page_url(setup: Setup) -> str:
    async with await connect(setup.url) as conn:
        order = await order_by_session(conn, setup.session_id)
    assert order is not None
    return f"http://mor/o/{order.buyer_token}"


async def _key(setup: Setup) -> dict[str, str]:
    async with await connect(setup.url) as conn:
        key = await create_api_key(conn, setup.shop.merchant_id, KeyEnv.TEST, NOW)
        await conn.commit()
    return {"authorization": f"Bearer {key.text}"}


def test_a_paid_order_gets_one_numbered_invoice_as_a_page_and_a_pdf(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        order_url = await _order_page_url(setup)

        # Paid, and the invoice not issued yet: the page waits for it.
        waiting = await setup.wired.control.get(order_url)
        assert waiting.status_code == 200
        assert "Your invoice is being prepared" in words(waiting.text)
        assert '<noscript><meta http-equiv="refresh"' in waiting.text
        status = await setup.wired.control.get(f"{order_url}/status")
        assert status.json() == {"status": "preparing"}
        missing = await setup.wired.control.get(f"{order_url}/invoice")
        assert missing.status_code == 404

        await drain(setup)
        page = await setup.wired.control.get(order_url)
        assert page.headers["referrer-policy"] == "no-referrer"
        assert page.headers["x-robots-tag"] == "noindex"
        assert page.headers["cache-control"] == "no-store"
        shown = words(page.text)
        assert "Thank you, your order is paid" in shown
        assert "View invoice INV-2026-000001" in shown
        assert "2 × Linen shirt" in shown
        assert "Tan Mei Ling" in shown and "1 Example Road" in shown
        assert "nothing more is due on delivery" in shown
        assert 'rel="noreferrer"' in page.text
        status = await setup.wired.control.get(f"{order_url}/status")
        assert status.json() == {"status": "invoiced"}

        invoice_page = await setup.wired.control.get(f"{order_url}/invoice")
        assert invoice_page.status_code == 200
        text = words(invoice_page.text)
        assert "Invoice INV-2026-000001" in text
        assert "Sold on behalf of Test Shop Co." in text
        assert "Linen shirt" in text and "Shipping:" in text
        (attempt,) = await attempts_of(setup)
        assert f"Total paid {money_text(attempt.amount)}" in text

        pdf = await setup.wired.control.get(f"{order_url}/invoice.pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF-")
        assert 'filename="INV-2026-000001.pdf"' in pdf.headers["content-disposition"]

        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT i.total_minor, sum(l.amount_minor)::bigint FROM invoice i"
                " JOIN invoice_line l ON l.invoice_id = i.id GROUP BY i.id"
            )
            rows = await cur.fetchall()
        assert rows == [(attempt.amount.amount_minor, attempt.amount.amount_minor)]

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_invoice_numbers_run_on_across_orders_and_a_redelivery_takes_none(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        second_id, option = await quoted_session(
            setup.url, setup.shop.merchant_id, SGD_PRICE, setup.clock[0]
        )
        second = Setup(
            url=setup.url,
            wired=setup.wired,
            clock=setup.clock,
            shop=setup.shop,
            session_id=second_id,
            option=option,
        )
        await approve_at_the_psp(second)
        await drain(second)

        async with await connect(setup.url) as conn:
            order = await order_by_session(conn, setup.session_id)
            assert order is not None
            # `order_paid` delivered again: the invoice is there already.
            again = await run_local(
                conn,
                ConsumerName.INVOICE_ISSUE,
                OrderPaidV1(
                    order_id=order.id,
                    checkout_session_id=order.session_id,
                    merchant_id=order.merchant_id,
                ),
                ENV,
                setup.clock[0],
            )
            assert isinstance(again, Stale)
            await conn.commit()
            cur = await conn.execute("SELECT number_text FROM invoice ORDER BY number")
            numbers = [row[0] for row in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT next_number FROM invoice_series WHERE code = 'INV'"
            )
            next_number = await cur.fetchone()
        assert numbers == ["INV-2026-000001", "INV-2026-000002"]
        assert next_number == (3,)

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_the_checkout_link_leads_to_the_order_page_only_while_the_session_lives(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        landing = await setup.wired.control.get(setup.page)
        assert landing.status_code == 303
        assert f"http://mor{landing.headers['location']}" == await _order_page_url(
            setup
        )

        setup.clock[0] = NOW + SESSION_LIFETIME
        later = await setup.wired.control.get(setup.page)
        assert later.status_code == 200
        shown = words(later.text)
        assert "Thank you, your order is paid" in shown
        assert "/o/" not in later.text
        # The order link itself still works: it is the buyer's receipt.
        kept = await setup.wired.control.get(await _order_page_url(setup))
        assert kept.status_code == 200

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_merchant_page_sends_the_paid_buyer_to_return_url_with_the_session_id(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        # Not yet paid: the checkout page as usual.
        assert (await setup.wired.control.get(setup.page)).status_code == 200
        await approve_at_the_psp(setup)
        await drain(setup)
        landing = await setup.wired.control.get(setup.page)
        assert landing.status_code == 303
        assert landing.headers["location"] == (
            f"https://shop.example/thanks?session_id=cs_{setup.session_id.hex}"
        )

    asyncio.run(
        with_setup(
            database_url,
            FAT_SALE_KEYED,
            body,
            after_payment=AfterPayment.MERCHANT_PAGE,
        )
    )


def test_a_merchant_without_approved_kyb_takes_no_payment(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        for state in (KybState.PENDING, KybState.SUSPENDED):
            async with await connect(setup.url) as conn:
                await set_kyb_state(conn, setup.shop.merchant_id, state, ACTOR, NOW)
                await conn.commit()
            page = words((await setup.wired.control.get(setup.page)).text)
            assert "This shop can't take payments right now" in page
            assert "Pay with" not in page
            refused = await pay_on_page(setup)
            assert refused.status_code == 303
            await drain(setup)
            assert await attempts_of(setup) == []
            assert await count_rows(setup.url, "SELECT count(*) FROM order_") == 0
            assert (
                await count_rows(
                    setup.url, "SELECT count(*) FROM fake_vendor.psp_fake_payment"
                )
                == 0
            )
        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT state::text FROM merchant_kyb_change ORDER BY id"
            )
            changes = [row[0] for row in await cur.fetchall()]
        # The shop's approval, then the two changes here, each recorded.
        assert changes == ["approved", "pending", "suspended"]

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_the_orders_api_shows_the_merchant_its_own_order_with_the_invoice(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        auth = await _key(setup)
        api = setup.wired.control
        session_url = f"http://mor/v1/checkout_sessions/cs_{setup.session_id.hex}"
        before = (await api.get(session_url, headers=auth)).json()
        assert before["order_id"] is None
        assert before["after_payment"] == "our_page"

        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        order_id = (await api.get(session_url, headers=auth)).json()["order_id"]
        assert order_id.startswith("ord_")
        order = (await api.get(f"http://mor/v1/orders/{order_id}", headers=auth)).json()
        assert order["invoice_number"] is None

        await drain(setup)
        answer = await api.get(f"http://mor/v1/orders/{order_id}", headers=auth)
        assert answer.status_code == 200
        order = answer.json()
        (attempt,) = await attempts_of(setup)
        assert order["state"] == "paid"
        assert order["invoice_number"] == "INV-2026-000001"
        assert order["total"] == {
            "amount_minor": attempt.amount.amount_minor,
            "currency": "SGD",
        }
        assert order["ship_to"]["name"] == "Tan Mei Ling"
        assert order["ship_to"]["email"] == "mei@example.test"
        assert order["ship_to"]["phone"] == "+6591234567"
        assert order["lines"][0]["merchant_sku"] == "LIN-01"
        assert order["merchant_reference"] == "order-1"
        assert order["order_url"] == await _order_page_url(setup)

        # Another merchant's order, or no such order, is not found.
        other = await _other_merchant_key(setup)
        hidden = await api.get(f"http://mor/v1/orders/{order_id}", headers=other)
        assert hidden.status_code == 404
        unknown = await api.get(
            f"http://mor/v1/orders/ord_{uuid.uuid7().hex}", headers=auth
        )
        assert unknown.status_code == 404
        assert (await api.get(f"http://mor/v1/orders/{order_id}")).status_code == 401

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


async def _other_merchant_key(setup: Setup) -> dict[str, str]:
    async with await connect(setup.url) as conn:
        other = await create_merchant(
            conn, "Other Shop Co.", "Other Shop", CountryCode("TH"), 100, ACTOR, NOW
        )
        key = await create_api_key(conn, other.id, KeyEnv.TEST, NOW)
        await conn.commit()
    return {"authorization": f"Bearer {key.text}"}


def test_the_merchant_sets_its_support_email_and_its_buyers_see_it(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        auth = await _key(setup)
        api = setup.wired.control
        bad = await api.post(
            "http://mor/v1/merchant/settings",
            json={"support_email": "not an email"},
            headers=auth,
        )
        assert bad.status_code == 400
        assert bad.json()["error"]["param"] == "support_email"
        set_ = await api.post(
            "http://mor/v1/merchant/settings",
            json={"support_email": "help@shop.example"},
            headers=auth,
        )
        assert set_.status_code == 200, set_.text
        assert set_.json()["support_email"] == "help@shop.example"
        assert set_.json()["kyb_state"] == "approved"

        await approve_at_the_psp(setup)
        await drain(setup)
        page = await api.get(await _order_page_url(setup))
        assert "mailto:help@shop.example" in page.text

        cleared = await api.post(
            "http://mor/v1/merchant/settings",
            json={"support_email": None},
            headers=auth,
        )
        assert cleared.json()["support_email"] is None
        page = await api.get(await _order_page_url(setup))
        assert "mailto:" not in page.text
        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT settings_actor_kind::text FROM merchant WHERE id = %s",
                (setup.shop.merchant_id,),
            )
            assert await cur.fetchone() == (ActorKind.MERCHANT_API_KEY.value,)

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_unknown_order_link_is_not_found(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        for path in ("", "/status", "/invoice", "/invoice.pdf"):
            answer = await setup.wired.control.get(f"http://mor/o/no-such{path}")
            assert answer.status_code == 404
            assert answer.headers["referrer-policy"] == "no-referrer"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


class _CrashBeforeCommit(Exception):
    pass


def test_a_crash_while_issuing_gives_the_invoice_number_back(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        crashing = await connect(setup.url, autocommit=True)
        try:
            (candidate,) = [
                found
                for found in await find_candidates(crashing, setup.clock[0], 50)
                if found.consumer == ConsumerName.INVOICE_ISSUE
            ]
            try:
                async with crashing.transaction():
                    await handle_delivery(
                        crashing,
                        candidate,
                        local_consumer(candidate),
                        ENV,
                        setup.clock[0],
                    )
                    raise _CrashBeforeCommit
            except _CrashBeforeCommit:
                pass
        finally:
            await crash(crashing)
        await drain(setup)
        async with await connect(setup.url) as conn:
            cur = await conn.execute("SELECT number_text FROM invoice")
            assert await cur.fetchall() == [("INV-2026-000001",)]

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))
