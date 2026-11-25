"""The staff Bank pages, the bank state on the Settlement page and the
sandbox's bank button (docs/plan-payments.md §7, the bank statement): a
request in, the page and the rows out. Every merchant, buyer and address
here is made up."""

import asyncio
import uuid

from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.ledger.account import BankSuspense, PayoutInTransit
from mor.messaging.guard import DuplicateGuard
from mor.psp.port import CaptureMode, SettlementMode, StatusMode
from tests.bank.support import amount_in, book_at_bank, cut, look_again, open_bank, paid
from tests.helpers import count_rows
from tests.payments.support import Setup, Wiring, with_setup, words


WIRING = Wiring(
    status_mode=StatusMode.PUSH_FAT,
    capture_mode=CaptureMode.SALE,
    guard=DuplicateGuard.VENDOR_CONSTRAINT,
    settlement_mode=SettlementMode.FILE,
)
SAME_SITE = {"origin": "http://mor"}


def _said(response_location: str) -> str:
    return response_location.replace("+", " ")


def test_a_person_links_a_credit_no_rule_matched_on_the_bank_page(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await cut(setup, "no_reference")
        await look_again(setup)
        await book_at_bank(setup, bank)
        await look_again(setup)
        page_url = f"http://mor/staff/bank/{bank.id}"

        listing = await setup.wired.control.get("http://mor/staff/bank")
        assert listing.status_code == 200
        text = words(listing.text)
        assert "agrees" in text and bank.name in text

        page = await setup.wired.control.get(page_url)
        assert page.status_code == 200
        assert "Link" in words(page.text)
        async with await connect(setup.url) as conn:
            entry_id = await one_value(
                conn, "SELECT id FROM bank_entry", None, uuid.UUID
            )
            payout_id = await one_value(
                conn, "SELECT id FROM settlement_payout", None, uuid.UUID
            )
        action = f"{page_url}/entries/{entry_id}/link"

        refused = await setup.wired.control.post(
            action, data={"payout": str(payout_id)}, headers={"origin": "http://evil"}
        )
        assert refused.status_code == 403
        linked = await setup.wired.control.post(
            action, data={"payout": str(payout_id)}, headers=SAME_SITE
        )
        assert linked.status_code == 303
        assert "linked" in _said(linked.headers["location"])
        again = await setup.wired.control.post(
            action, data={"payout": str(payout_id)}, headers=SAME_SITE
        )
        assert "not linked" in _said(again.headers["location"])

        assert await count_rows(setup.url, "SELECT count(*) FROM bank_match") == 1
        assert await amount_in(setup, BankSuspense(bank.id)) == 0
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == 0
        page = await setup.wired.control.get(page_url)
        assert "staff-bank-page" in words(page.text)

    asyncio.run(with_setup(database_url, WIRING, body))


def test_an_entry_no_payout_explains_takes_one_note_and_books_nothing(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await cut(setup, "unexpected_debit")
        await look_again(setup)
        await book_at_bank(setup, bank)
        await look_again(setup)
        page_url = f"http://mor/staff/bank/{bank.id}"
        async with await connect(setup.url) as conn:
            entry_id = await one_value(
                conn,
                "SELECT id FROM bank_entry WHERE amount_minor < 0",
                None,
                uuid.UUID,
            )
        action = f"{page_url}/entries/{entry_id}/note"
        entries_before = await count_rows(
            setup.url, "SELECT count(*) FROM journal_entry"
        )

        empty = await setup.wired.control.post(
            action, data={"note": " "}, headers=SAME_SITE
        )
        assert "write what it is" in _said(empty.headers["location"])
        for note in ("the bank's monthly fee", "a second note"):
            sent = await setup.wired.control.post(
                action, data={"note": note}, headers=SAME_SITE
            )
            assert sent.status_code == 303
        text = words((await setup.wired.control.get(page_url)).text)
        assert "the bank's monthly fee" in text
        assert "a second note" not in text
        assert (
            await count_rows(setup.url, "SELECT count(*) FROM journal_entry")
            == entries_before
        )

    asyncio.run(with_setup(database_url, WIRING, body))


def test_a_payout_whose_credit_arrived_short_is_flagged_and_takes_a_note(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await cut(setup, "short")
        await look_again(setup)
        page_url = f"http://mor/staff/settlement/{setup.shop.account.id}"
        text = words((await setup.wired.control.get(page_url)).text)
        assert "not yet" in text and "flagged:" not in text

        await book_at_bank(setup, bank)
        await look_again(setup)
        text = words((await setup.wired.control.get(page_url)).text)
        assert "flagged:" in text and "off by -15.00 SGD" in text

        async with await connect(setup.url) as conn:
            payout_id = await one_value(
                conn, "SELECT id FROM settlement_payout", None, uuid.UUID
            )
        sent = await setup.wired.control.post(
            f"{page_url}/payouts/{payout_id}/note",
            data={"note": "correspondent bank charge"},
            headers=SAME_SITE,
        )
        assert sent.status_code == 303
        text = words((await setup.wired.control.get(page_url)).text)
        assert "correspondent bank charge" in text and "flagged:" not in text

    asyncio.run(with_setup(database_url, WIRING, body))


def test_the_sandbox_books_the_banks_day(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await cut(setup)
        page = await setup.wired.control.get("http://mor/_fake/")
        assert f"book {bank.name}" in words(page.text)
        booked = await setup.wired.control.post(
            "http://mor/_fake/sandbox/bank-book",
            data={"bank_account_id": str(bank.id)},
            headers=SAME_SITE,
        )
        assert booked.status_code == 303
        assert "answered 200" in _said(booked.headers["location"])
        await look_again(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM bank_match") == 1

    asyncio.run(with_setup(database_url, WIRING, body))
