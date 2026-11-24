"""The Settlement staff pages and the sandbox's cut (docs/plan-payments.md
§7.1, docs/plan-payments.md §7): a request in, the page
and the rows out. Every merchant, buyer and
address here is made up."""

import asyncio
import uuid

from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.messaging.guard import DuplicateGuard
from mor.psp.port import CaptureMode, SettlementMode, StatusMode
from mor.settlement.fetch import start_settlement_reading
from tests.helpers import count_rows
from tests.payments.support import (
    ACTOR,
    NOW,
    Setup,
    Wiring,
    approve_at_the_psp,
    drain,
    with_setup,
    words,
)


WIRING = Wiring(
    status_mode=StatusMode.PUSH_FAT,
    capture_mode=CaptureMode.SALE,
    guard=DuplicateGuard.VENDOR_CONSTRAINT,
    settlement_mode=SettlementMode.FILE,
)
SAME_SITE = {"origin": "http://mor"}


async def _settled_with(setup: Setup, fault: str) -> None:
    """A paid order, then a settlement cut on the sandbox page and read."""
    await approve_at_the_psp(setup)
    await drain(setup)
    async with await connect(setup.url) as conn:
        await start_settlement_reading(conn, setup.shop.account.id, ACTOR, NOW)
        await conn.commit()
    cut = await setup.wired.control.post(
        "http://mor/_fake/sandbox/settlement",
        data={"account_id": str(setup.shop.account.id), "fault": fault},
        headers=SAME_SITE,
    )
    assert cut.status_code == 303, cut.text
    assert "answered 200" in cut.headers["location"].replace("+", " ")
    setup.clock[0] += setup.shop.account.settlement_interval
    await drain(setup)


def test_the_staff_page_shows_an_exception_and_resolving_records_one_note(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        # An adjustment of -5.00 SGD the PSP took: an exception, the payout
        # still adds up.
        await _settled_with(setup, "unknown_type")
        account_page = f"http://mor/staff/settlement/{setup.shop.account.id}"

        listing = await setup.wired.control.get("http://mor/staff/settlement")
        assert listing.status_code == 200
        assert "clearing 5.00 SGD" in words(listing.text)

        page = await setup.wired.control.get(account_page)
        assert page.status_code == 200
        text = words(page.text)
        assert "unknown_type" in text and "Resolve" in text
        async with await connect(setup.url) as conn:
            exception_id = await one_value(
                conn, "SELECT id FROM settlement_exception", None, uuid.UUID
            )
        action = f"{account_page}/exceptions/{exception_id}"

        refused = await setup.wired.control.post(
            action, data={"note": "looked"}, headers={"origin": "http://evil"}
        )
        assert refused.status_code == 403
        empty = await setup.wired.control.post(
            action, data={"note": "  "}, headers=SAME_SITE
        )
        assert empty.status_code == 303
        assert "write what was found" in empty.headers["location"].replace("+", " ")

        for note in ("PSP kept 5.00 SGD; asked for a correction", "a second note"):
            sent = await setup.wired.control.post(
                action, data={"note": note}, headers=SAME_SITE
            )
            assert sent.status_code == 303
        page = await setup.wired.control.get(account_page)
        text = words(page.text)
        assert "PSP kept 5.00 SGD; asked for a correction" in text
        assert "a second note" not in text
        assert (
            await count_rows(
                setup.url, "SELECT count(*) FROM settlement_exception_resolved"
            )
            == 1
        )
        # A note books nothing.
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM journal_entry WHERE actor_kind = 'staff'",
            )
            == 0
        )

    asyncio.run(with_setup(database_url, WIRING, body))


def test_a_clean_settlement_leaves_nothing_to_resolve(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await _settled_with(setup, "")
        page = await setup.wired.control.get(
            f"http://mor/staff/settlement/{setup.shop.account.id}"
        )
        text = words(page.text)
        assert "No exceptions." in text
        assert "clearing 0.00 SGD" in text
        assert "1 of 1" in text  # lines booked

    asyncio.run(with_setup(database_url, WIRING, body))


def test_a_payout_that_does_not_add_up_is_shown_open_not_raised(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await _settled_with(setup, "net_differs")
        listing = await setup.wired.control.get("http://mor/staff/settlement")
        assert "clearing 1.00 SGD" in words(listing.text)
        page = await setup.wired.control.get(
            f"http://mor/staff/settlement/{setup.shop.account.id}"
        )
        text = words(page.text)
        assert "No exceptions." in text
        # How long it has been open depends on the clock; the gap does not.
        assert "open " in text and "off by -1.00 SGD" in text

    asyncio.run(with_setup(database_url, WIRING, body))
