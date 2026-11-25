"""Shared setup for the bank tests (docs/plan-payments.md §7, the bank
statement): a shop paid through the fake PSP, an SGD account at the fake
bank that the PSP account pays out into, on both sides, and the steps a day
takes: cut, read the settlement, book at the bank, read the bank. Every
merchant, buyer and address here is made up."""

import dataclasses

from mor.bank.accounts import create_bank_account, set_payout_destination
from mor.bank.fetch import start_reading
from mor.bank.port import BankAccount, BankProvider
from mor.db.connect import connect
from mor.db.fetch import one_value
from mor.ledger.account import Bank, BankSuspense, PayoutInTransit
from mor.ledger.store import balance
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind
from mor.settlement.fetch import start_settlement_reading
from tests.helpers import VENDOR_TIMEOUT
from tests.payments.support import (
    ACTOR,
    NOW,
    SGD_PRICE,
    Setup,
    approve_at_the_psp,
    drain,
    quoted_session,
    session_state,
)


BANK = "bank-sgd"


async def open_bank(setup: Setup, read_settlement: bool = True) -> BankAccount:
    """Our SGD account at the fake bank, the PSP account paying out into it
    on both sides, and the bank's reader started; the PSP's settlement
    reader too unless the test starts it later (`start_settlement`)."""
    async with await connect(setup.url) as conn:
        bank = await create_bank_account(
            conn,
            BANK,
            BankProvider.FAKE,
            ProviderAccountKind.FAKE,
            f"http://mor/_fake/bank/{BANK}",
            "FAKE-SGD-0001",
            Currency.SGD,
            NOW,
            VENDOR_TIMEOUT,
        )
        await set_payout_destination(conn, setup.shop.account.id, bank, NOW)
        await start_reading(conn, bank.id, ACTOR, NOW)
        if read_settlement:
            await start_settlement_reading(conn, setup.shop.account.id, ACTOR, NOW)
        await conn.commit()
    control = setup.wired.control
    opened = await control.post(f"{bank.base_url}/setup", json={"currency": "SGD"})
    assert opened.status_code == 204, opened.text
    pointed = await control.post(
        f"{bank.base_url}/destinations",
        json={"psp_account": setup.shop.account.name},
    )
    assert pointed.status_code == 204, pointed.text
    return bank


async def start_settlement(setup: Setup) -> None:
    async with await connect(setup.url) as conn:
        assert await start_settlement_reading(
            conn, setup.shop.account.id, ACTOR, setup.clock[0]
        )
        await conn.commit()


async def pay_again(setup: Setup) -> Setup:
    """Another session of the same shop, paid by card and drained."""
    session_id, option = await quoted_session(
        setup.url, setup.shop.merchant_id, SGD_PRICE, setup.clock[0]
    )
    other = dataclasses.replace(setup, session_id=session_id, option=option)
    await approve_at_the_psp(other)
    await drain(other)
    assert await session_state(other) == "completed"
    return other


async def paid(setup: Setup) -> None:
    await approve_at_the_psp(setup)
    await drain(setup)


async def cut(setup: Setup, *bank_faults: str) -> list[str]:
    response = await setup.wired.control.post(
        f"http://mor/_fake/psp/{setup.shop.account.name}/settlements",
        json={"bank_faults": list(bank_faults)},
    )
    assert response.status_code == 200, response.text
    return response.json()["batches"]


async def look_again(setup: Setup) -> None:
    """Past both readers' interval: the next looks run."""
    setup.clock[0] += setup.shop.account.settlement_interval
    await drain(setup)


async def book_at_bank(setup: Setup, bank: BankAccount) -> list[str]:
    response = await setup.wired.control.post(f"{bank.base_url}/book")
    assert response.status_code == 200, response.text
    return response.json()["booked"]


async def amount_in(
    setup: Setup, account: Bank | BankSuspense | PayoutInTransit
) -> int:
    async with await connect(setup.url) as conn:
        found = await balance(conn, account, Currency.SGD)
    return found.amount_minor


async def bank_says(setup: Setup, bank: BankAccount) -> int:
    """The fake bank's own balance."""
    async with await connect(setup.url) as conn:
        return await one_value(
            conn,
            "SELECT balance_minor FROM fake_vendor.bank_fake_account WHERE name = %s",
            (bank.name,),
            int,
        )
