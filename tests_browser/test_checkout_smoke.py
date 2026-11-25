"""The buyer's checkout in a real browser, end to end against the smoke
setup (tests_browser/conftest.py; docs/plan-payments.md §4, card fields):
under the CSP the app sends, each journey fails on any "Refused to …" in
the console or any error on the page. Every buyer and address here is
made up."""

import re
from collections.abc import Iterator
from dataclasses import dataclass

import httpx2
import psycopg
import pytest
from playwright.sync_api import (
    Browser,
    ConsoleMessage,
    Error,
    Page,
    expect,
    sync_playwright,
)

from tests_browser.conftest import DATABASE, Smoke


ADDRESS = {
    "Full name": "Tan Mei Ling",
    "Email": "mei@example.test",
    "Address": "1 Example Road",
    "City or district": "Singapore",
    "Postcode": "123456",
    "Phone number": "9123 4567",
}
SESSION = {
    "lines": [
        {
            "name": "Linen shirt",
            "quantity": 2,
            "unit_price": {"amount_minor": 2500, "currency": "SGD"},
            "category": "apparel",
            "customs": {
                "description": "Men's linen shirt, woven",
                "origin_country": "VN",
                "weight_g": 300,
                "hs_code": "610910",
            },
        }
    ],
    "parcel": {"weight_g": 1000, "length_mm": 200, "width_mm": 150, "height_mm": 100},
    "return_url": "https://shop.example/thanks",
    "cancel_url": "https://shop.example/cart",
}
# Long enough for the worker, the PSP's webhook and the page's next look.
DECIDED_WITHIN_MS = 30_000


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def _checkout(smoke: Smoke, reference: str) -> str:
    """A new checkout session of the Sandbox Shop: its page."""
    answer = httpx2.post(
        f"{smoke.self_url}/v1/checkout_sessions",
        json=SESSION | {"merchant_reference": reference},
        headers={
            "Authorization": f"Bearer {smoke.api_key}",
            "Idempotency-Key": reference,
        },
    )
    assert answer.status_code == 201, answer.text
    return str(answer.json()["url"])


@dataclass(frozen=True)
class Watched:
    """A page, and what its console said against the CSP and every error
    its scripts raised, as they come."""

    page: Page
    refused: list[str]


def _open(browser: Browser, url: str, javascript: bool = True) -> Watched:
    context = browser.new_context(java_script_enabled=javascript)
    page = context.new_page()
    refused: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if "Refused to" in message.text or "Content Security Policy" in message.text:
            refused.append(message.text)

    def on_error(error: Error) -> None:
        refused.append(f"error: {error}")

    page.on("console", on_console)
    page.on("pageerror", on_error)
    page.goto(url)
    return Watched(page, refused)


def _quote(page: Page) -> None:
    page.get_by_role("combobox", name=re.compile("^Country")).select_option(
        label="Singapore"
    )
    for label, value in ADDRESS.items():
        page.get_by_role("textbox", name=label, exact=True).fill(value)
    page.get_by_role("button", name="See the price").click()


def _pay(page: Page, method: str) -> None:
    page.get_by_role("radio", name=re.compile(method)).check()
    page.get_by_role("button", name=re.compile(r"^Pay ")).click()


def _type_card(page: Page, number: str) -> None:
    frame = page.frame_locator("#card-fields iframe")
    frame.get_by_label("Card number").fill(number)
    frame.get_by_label("Expiry (MM/YY)").fill("12/30")
    frame.get_by_label("CVC").fill("123")
    frame.get_by_role("button", name="Pay").click()


def _ends_on_the_order_page(watched: Watched) -> None:
    watched.page.wait_for_url(re.compile(r"/o/"), timeout=DECIDED_WITHIN_MS)
    expect(watched.page.get_by_text("invoice", exact=False).first).to_be_visible()
    assert watched.refused == [], watched.refused


def test_a_card_typed_in_the_psps_fields_ends_on_the_order_page(
    smoke: Smoke, browser: Browser
) -> None:
    watched = _open(browser, _checkout(smoke, "smoke-card"))
    _quote(watched.page)
    _pay(watched.page, "Card")
    # Only the card form: the link to the PSP's page is hidden.
    expect(watched.page.locator("#card-fields iframe")).to_be_visible()
    expect(watched.page.get_by_role("link", name="Continue to payment")).to_be_hidden()
    _type_card(watched.page, "4242424242424242")
    _ends_on_the_order_page(watched)


def test_a_declined_card_lets_the_buyer_pay_again(
    smoke: Smoke, browser: Browser
) -> None:
    watched = _open(browser, _checkout(smoke, "smoke-declined"))
    _quote(watched.page)
    _pay(watched.page, "Card")
    _type_card(watched.page, "4000000000000002")
    expect(watched.page.get_by_text("Your payment was declined")).to_be_visible(
        timeout=DECIDED_WITHIN_MS
    )
    _pay(watched.page, "Card")
    _type_card(watched.page, "4242424242424242")
    _ends_on_the_order_page(watched)


def test_a_card_that_asks_for_3ds_is_paid_once_passed(
    smoke: Smoke, browser: Browser
) -> None:
    watched = _open(browser, _checkout(smoke, "smoke-3ds"))
    _quote(watched.page)
    _pay(watched.page, "Card")
    _type_card(watched.page, "4000000000003220")
    watched.page.get_by_role("button", name="Complete authentication").click()
    _ends_on_the_order_page(watched)


def test_without_javascript_the_buyer_pays_on_the_psps_page(
    smoke: Smoke, browser: Browser
) -> None:
    watched = _open(browser, _checkout(smoke, "smoke-nojs"), javascript=False)
    _quote(watched.page)
    _pay(watched.page, "Card")
    watched.page.get_by_role("link", name="Continue to payment").click()
    watched.page.get_by_role("button", name="Approve").click()
    _ends_on_the_order_page(watched)


def test_a_qr_payment_ends_on_the_order_page(smoke: Smoke, browser: Browser) -> None:
    watched = _open(browser, _checkout(smoke, "smoke-qr"))
    _quote(watched.page)
    _pay(watched.page, "QR")
    expect(watched.page.get_by_text("Scan this code")).to_be_visible()
    # The buyer's phone scans the code: the fake's page for that payment.
    with psycopg.connect(f"postgresql:///{DATABASE}") as conn:
        row = conn.execute(
            "SELECT id FROM fake_vendor.psp_fake_payment"
            " WHERE method = 'qr' AND status = 'requires_action'"
            " ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    phone = watched.page.context.new_page()
    phone.goto(f"{smoke.self_url}/_fake/psp/pay/{row[0]}")
    phone.get_by_role("button", name="Pay from the app").click()
    phone.close()
    _ends_on_the_order_page(watched)


def test_the_staff_pages_load_under_the_policy(smoke: Smoke, browser: Browser) -> None:
    """Each staff page, the quote's share bar among them, with rows from
    the journeys above."""
    watched = _open(browser, f"{smoke.self_url}/staff/markets")
    for link in (
        "/staff/markets?country=SG&goods=500.00&currency=SGD&hs=610910"
        "&origin=VN&weight=1000&length=200&width=150&height=100",
        "/staff/markets/answers",
        "/staff/markets/data",
        "/staff/markets/sessions",
        "/staff/settlement",
        "/staff/bank",
    ):
        answer = watched.page.goto(f"{smoke.self_url}{link}")
        assert answer is not None and answer.status == 200, link
    assert watched.refused == [], watched.refused
