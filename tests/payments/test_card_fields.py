"""Card fields on the buyer's page (docs/plan-payments.md §4, card fields):
the page leaves room for the PSP's form and names its scripts, the card is
typed in the PSP's frame and decided by the PSP, and the app's CSP allows
exactly the PSP's origin. A request in, the page, the header and the rows
out; the browser journeys are in tests_browser/. Every merchant, buyer
and address here is made up."""

import asyncio
import html

from mor.fakes.psp import CARD_APPROVES, CARD_ASKS_3DS, CARD_DECLINES
from mor.payments.transitions import AttemptState
from tests.payments.support import (
    FAT_SALE_KEYED,
    Setup,
    attempts_of,
    drain,
    pay_on_page,
    session_state,
    with_setup,
    words,
)


# The fake PSP's browser origin in the in-process wiring (tests/helpers.py).
PSP_ORIGIN = "http://psp.mor"


async def _waiting_on_card(setup: Setup) -> str:
    """Paid by card on our page: the fields' secret."""
    paid = await pay_on_page(setup)
    assert paid.status_code == 303, paid.text
    await drain(setup)
    (attempt,) = await attempts_of(setup)
    assert attempt.state == AttemptState.ACTION_REQUIRED
    assert attempt.action is not None and attempt.action.fields_secret is not None
    return attempt.action.fields_secret


async def _type_card(setup: Setup, secret: str, number: str) -> int:
    typed = await setup.wired.control.post(
        f"http://mor/_fake/psp/fields/{secret}", data={"number": number}
    )
    if typed.status_code == 303:
        assert typed.headers["location"] == setup.page
    return typed.status_code


def test_the_pay_step_leaves_room_for_the_psps_form_and_keeps_its_page(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        secret = await _waiting_on_card(setup)
        page = html.unescape((await setup.wired.control.get(setup.page)).text)
        assert f'id="card-fields" class="card-fields" data-secret="{secret}"' in page
        assert f'<script src="{PSP_ORIGIN}/_fake/psp/js/fields.js">' in page
        assert "/static/js/psp_fake_fields.js" in page
        # Without JavaScript the buyer still pays on the PSP's page.
        assert "Continue to payment" in words(page)

        form = await setup.wired.control.get(f"http://mor/_fake/psp/fields/{secret}")
        assert form.status_code == 200
        assert 'target="_top"' in form.text
        unknown = await setup.wired.control.get("http://mor/_fake/psp/fields/x_secret")
        assert unknown.status_code == 404

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_qr_payment_shows_no_card_form(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await pay_on_page(setup, "qr")
        await drain(setup)
        page = (await setup.wired.control.get(setup.page)).text
        assert "card-fields" not in page
        assert "fields.js" not in page

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_card_typed_in_the_psps_frame_is_decided_by_the_psp(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        secret = await _waiting_on_card(setup)
        assert await _type_card(setup, secret, "4000 0000 0000 0002") == 303
        assert CARD_DECLINES == "4000000000000002"
        await drain(setup)
        assert await session_state(setup) == "quoted"
        page = words((await setup.wired.control.get(setup.page)).text)
        assert "Your payment was declined. You can try again." in page

        secret = await _waiting_on_card_again(setup)
        assert await _type_card(setup, secret, CARD_APPROVES) == 303
        await drain(setup)
        assert await session_state(setup) == "completed"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


async def _waiting_on_card_again(setup: Setup) -> str:
    await pay_on_page(setup)
    await drain(setup)
    attempt = (await attempts_of(setup))[-1]
    assert attempt.action is not None and attempt.action.fields_secret is not None
    return attempt.action.fields_secret


def test_a_card_that_asks_for_3ds_is_paid_once_the_buyer_passes_it(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        secret = await _waiting_on_card(setup)
        challenge = await setup.wired.control.post(
            f"http://mor/_fake/psp/fields/{secret}", data={"number": CARD_ASKS_3DS}
        )
        assert challenge.status_code == 200
        assert "Complete authentication" in words(challenge.text)
        (attempt,) = await attempts_of(setup)
        assert attempt.action is not None
        passed = await setup.wired.control.post(
            attempt.action.url, data={"outcome": "paid"}
        )
        assert passed.status_code == 303
        await drain(setup)
        assert await session_state(setup) == "completed"

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_an_unknown_card_number_is_refused_and_decides_nothing(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        secret = await _waiting_on_card(setup)
        assert await _type_card(setup, secret, "1234") == 400
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.ACTION_REQUIRED

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))
