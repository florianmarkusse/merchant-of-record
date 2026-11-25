"""The bank statement through its edges (docs/plan-payments.md §7, the bank
statement): the fake PSP pays out, the fake bank books the credit, the
worker reads both, and the rows and the ledger come out. Every merchant,
buyer and address here is made up."""

import asyncio

import pytest

from mor.bank.matching import (
    Linked,
    NotLinkable,
    link,
    lock_bank_account,
)
from mor.bank.port import BankAccount
from mor.db.connect import connect
from mor.fakes.bank import CHARGE_MINOR, DEBIT_MINOR, UNANNOUNCED_MINOR
from mor.ids import BankEntryId, SettlementPayoutId
from mor.ledger.account import Bank, BankSuspense, PayoutInTransit
from mor.messaging.guard import DuplicateGuard
from mor.psp.port import CaptureMode, SettlementMode, StatusMode
from tests.bank.support import (
    amount_in,
    bank_says,
    book_at_bank,
    cut,
    look_again,
    open_bank,
    paid,
    pay_again,
    start_settlement,
)
from tests.helpers import count_rows
from tests.payments.support import ACTOR, Setup, Wiring, with_setup


def _wiring(mode: SettlementMode) -> Wiring:
    return Wiring(
        status_mode=StatusMode.PUSH_FAT,
        capture_mode=CaptureMode.SALE,
        guard=DuplicateGuard.VENDOR_CONSTRAINT,
        settlement_mode=mode,
    )


async def _net(setup: Setup) -> int:
    async with await connect(setup.url) as conn:
        cur = await conn.execute("SELECT net_minor FROM settlement_payout")
        rows = await cur.fetchall()
    assert len(rows) == 1
    return int(rows[0][0])


async def _matches(setup: Setup) -> int:
    return await count_rows(setup.url, "SELECT count(*) FROM bank_match")


async def _the_day(setup: Setup, bank: BankAccount, *bank_faults: str) -> None:
    """A cut, its payout read, the bank's day booked and read."""
    await cut(setup, *bank_faults)
    await look_again(setup)
    await book_at_bank(setup, bank)
    await look_again(setup)


@pytest.mark.parametrize("mode", list(SettlementMode))
def test_a_payout_matched_to_its_credit_moves_from_transit_to_the_bank(
    database_url: str, mode: SettlementMode
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await pay_again(setup)
        await _the_day(setup, bank)

        net = await _net(setup)
        assert await _matches(setup) == 1
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == 0
        assert await amount_in(setup, BankSuspense(bank.id)) == 0
        assert await amount_in(setup, Bank(bank.id)) == net
        assert await bank_says(setup, bank) == net

        # Read again: nothing new, nothing booked twice.
        await look_again(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM bank_entry") == 1
        assert await _matches(setup) == 1

    asyncio.run(with_setup(database_url, _wiring(mode), body))


def test_a_credit_read_before_its_payout_waits_in_suspense_then_matches(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup, read_settlement=False)
        await paid(setup)
        await cut(setup)
        await book_at_bank(setup, bank)
        await look_again(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM bank_entry") == 1
        assert await _matches(setup) == 0
        credit = await amount_in(setup, Bank(bank.id))
        assert await amount_in(setup, BankSuspense(bank.id)) == -credit

        await start_settlement(setup)
        await look_again(setup)

        assert await _matches(setup) == 1
        assert await amount_in(setup, BankSuspense(bank.id)) == 0
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == 0

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


@pytest.mark.parametrize("fault", ["no_reference", "reference_cut"])
def test_a_credit_without_its_reference_stays_in_suspense_for_a_person(
    database_url: str, fault: str
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await _the_day(setup, bank, fault)

        net = await _net(setup)
        assert await _matches(setup) == 0
        assert await amount_in(setup, Bank(bank.id)) == net
        assert await amount_in(setup, BankSuspense(bank.id)) == -net
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == net

        # A person links it: booked as a rule's match would be.
        async with await connect(setup.url) as conn:
            cur = await conn.execute("SELECT id FROM bank_entry")
            entry_id = BankEntryId((await cur.fetchall())[0][0])
            cur = await conn.execute("SELECT id FROM settlement_payout")
            payout_id = SettlementPayoutId((await cur.fetchall())[0][0])
            account = await lock_bank_account(conn, bank.id)
            assert (
                await link(conn, account, entry_id, payout_id, ACTOR, setup.clock[0])
                == Linked()
            )
            await conn.commit()
        assert await amount_in(setup, BankSuspense(bank.id)) == 0
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == 0

        # Linked once: a second link is refused and books nothing.
        async with await connect(setup.url) as conn:
            account = await lock_bank_account(conn, bank.id)
            again = await link(
                conn, account, entry_id, payout_id, ACTOR, setup.clock[0]
            )
            await conn.commit()
        assert isinstance(again, NotLinkable)
        assert await _matches(setup) == 1

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_short_credit_is_matched_and_the_charge_stays_in_transit(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await _the_day(setup, bank, "short")

        net = await _net(setup)
        assert await _matches(setup) == 1
        assert await amount_in(setup, Bank(bank.id)) == net - CHARGE_MINOR
        assert await amount_in(setup, BankSuspense(bank.id)) == 0
        assert (
            await amount_in(setup, PayoutInTransit(setup.shop.account.id))
            == CHARGE_MINOR
        )

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_credit_that_never_arrives_leaves_the_payout_in_transit(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await _the_day(setup, bank, "never_arrives")

        assert await count_rows(setup.url, "SELECT count(*) FROM bank_entry") == 0
        assert await amount_in(
            setup, PayoutInTransit(setup.shop.account.id)
        ) == await _net(setup)

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_transaction_listed_twice_is_booked_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await _the_day(setup, bank, "listed_twice")

        assert await count_rows(setup.url, "SELECT count(*) FROM bank_entry") == 1
        assert await _matches(setup) == 1
        assert await amount_in(setup, Bank(bank.id)) == await bank_says(setup, bank)

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


@pytest.mark.parametrize(
    ("fault", "stray"),
    [("unannounced", UNANNOUNCED_MINOR), ("unexpected_debit", -DEBIT_MINOR)],
)
def test_an_entry_no_payout_explains_is_booked_and_left_in_suspense(
    database_url: str, fault: str, stray: int
) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await _the_day(setup, bank, fault)

        net = await _net(setup)
        assert await _matches(setup) == 1
        assert await amount_in(setup, Bank(bank.id)) == net + stray
        assert await amount_in(setup, Bank(bank.id)) == await bank_says(setup, bank)
        assert await amount_in(setup, BankSuspense(bank.id)) == -stray
        assert await amount_in(setup, PayoutInTransit(setup.shop.account.id)) == 0

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_bank_that_fails_a_read_is_read_again_later(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        bank = await open_bank(setup)
        await paid(setup)
        await cut(setup)
        await look_again(setup)
        await book_at_bank(setup, bank)
        for fault in ("fail_500", "timeout"):
            scripted = await setup.wired.control.post(
                "http://mor/_fake/bank/faults",
                json={"account": bank.name, "fault": fault},
            )
            assert scripted.status_code == 204
        await look_again(setup)
        await look_again(setup)

        assert await _matches(setup) == 1
        assert await amount_in(setup, Bank(bank.id)) == await bank_says(setup, bank)

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))
