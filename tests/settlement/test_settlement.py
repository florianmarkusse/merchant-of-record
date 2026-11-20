"""Settlement through its edges (docs/plan-payments.md §7.1): the fake PSP
cuts a batch, the worker reads it by report file or by API, and the rows
and the ledger come out. Every merchant, buyer and address here is made
up."""

import asyncio
import dataclasses
from datetime import timedelta

import pytest

from mor.db.connect import connect
from mor.ledger.account import Account, AccountKind
from mor.ledger.store import balance
from mor.messaging.facts import SettlementBatchReceivedV1, SettlementLineReceivedV1
from mor.messaging.guard import DuplicateGuard
from mor.messaging.outcome import Stale
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import Failed, find_candidates, run_one
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.inbound import store_inbox
from mor.psp.port import CaptureMode, InboundMode, SettlementMode, StatusMode
from mor.settlement.fetch import start_settlement_reading
from mor.settlement.store import book_lines, book_payout
from tests.helpers import ENV
from tests.payments.support import (
    ACTOR,
    NOW,
    SGD_PRICE,
    Setup,
    Wiring,
    approve_at_the_psp,
    attempts_of,
    count_rows,
    drain,
    pay_on_page,
    quoted_session,
    release,
    session_state,
    with_setup,
)


def _wiring(mode: SettlementMode, status: StatusMode = StatusMode.PUSH_FAT) -> Wiring:
    return Wiring(status, CaptureMode.SALE, DuplicateGuard.VENDOR_CONSTRAINT, mode)


async def _pay(setup: Setup) -> Setup:
    """Another session of the same shop, paid by card and drained."""
    session_id, option = await quoted_session(
        setup.url, setup.shop.merchant_id, SGD_PRICE, setup.clock[0]
    )
    other = dataclasses.replace(setup, session_id=session_id, option=option)
    await approve_at_the_psp(other)
    await drain(other)
    assert await session_state(other) == "completed"
    return other


async def _start_reading(setup: Setup) -> None:
    async with await connect(setup.url) as conn:
        assert await start_settlement_reading(conn, setup.shop.account.id, ACTOR, NOW)
        await conn.commit()


async def _cut(setup: Setup, *faults: str) -> list[str]:
    response = await setup.wired.control.post(
        f"http://mor/_fake/psp/{setup.shop.account.name}/settlements",
        json={"faults": list(faults)},
    )
    assert response.status_code == 200, response.text
    return response.json()["batches"]


async def _read_settlement(setup: Setup) -> None:
    """The next look is due after the account's interval: move past it."""
    setup.clock[0] += setup.shop.account.settlement_interval
    await drain(setup)


async def _balance(setup: Setup, kind: AccountKind) -> int:
    async with await connect(setup.url) as conn:
        found = await balance(
            conn, Account(kind, str(setup.shop.account.id)), Currency.SGD
        )
    return found.amount_minor


async def _exceptions(setup: Setup) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT kind::text FROM settlement_exception ORDER BY kind"
        )
        return [row[0] for row in await cur.fetchall()]


async def _line_states(setup: Setup) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute("SELECT state::text FROM settlement_line ORDER BY id")
        return [row[0] for row in await cur.fetchall()]


async def _fees(setup: Setup) -> tuple[int, int]:
    """The fees the fake kept and the estimates the captures booked."""
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT coalesce(sum(fee_minor), 0)::bigint FROM settlement_line"
        )
        row = await cur.fetchone()
        assert row is not None
        cur = await conn.execute(
            """
            SELECT coalesce(-sum(l.amount_minor), 0)::bigint FROM journal_line l
            JOIN journal_entry e ON e.id = l.entry_id
            WHERE l.account_kind = 'psp_fee_collected'
              AND e.idempotency_key LIKE 'capture:%'
            """
        )
        estimate = await cur.fetchone()
        assert estimate is not None
    return row[0], estimate[0]


@pytest.mark.parametrize("mode", list(SettlementMode))
def test_a_cut_settles_every_payment_once_and_clears_psp_clearing(
    database_url: str, mode: SettlementMode
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        # Three payments: an API batch then needs two pages of two lines.
        await _pay(setup)
        await _pay(setup)
        await _start_reading(setup)
        await drain(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM settlement_batch") == 0

        batches = await _cut(setup)
        assert len(batches) == 1
        await _read_settlement(setup)

        assert await _line_states(setup) == ["booked", "booked", "booked"]
        assert await _exceptions(setup) == []
        captured = 3 * (await attempts_of(setup))[0].amount.amount_minor
        fees, estimates = await _fees(setup)
        assert await _balance(setup, AccountKind.PSP_CLEARING) == 0
        assert await _balance(setup, AccountKind.PAYOUT_IN_TRANSIT) == captured - fees
        # The estimate cleared, the difference is ours.
        assert await _balance(setup, AccountKind.PSP_FEE_VARIANCE) == fees - estimates
        async with await connect(setup.url) as conn:
            collected = await balance(
                conn, Account(AccountKind.PSP_FEE_COLLECTED), Currency.SGD
            )
        assert collected == Money(0, Currency.SGD)

        # Read again: nothing new, nothing booked twice.
        await _read_settlement(setup)
        assert await _cut(setup) == []
        await _read_settlement(setup)
        assert await count_rows(setup.url, "SELECT count(*) FROM settlement_line") == 3
        assert (
            await count_rows(setup.url, "SELECT count(*) FROM journal_entry")
            == 3 + 3 + 1
        )

    asyncio.run(with_setup(database_url, _wiring(mode), body))


@pytest.mark.parametrize(
    ("fault", "kind"),
    [
        ("unknown_reference", "unknown_reference"),
        ("gross_differs", "amount_differs"),
        ("unknown_type", "unknown_type"),
        ("net_differs", "net_differs"),
    ],
)
def test_each_fault_in_a_batch_is_one_exception_and_the_rest_is_booked(
    database_url: str, fault: str, kind: str
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        await _pay(setup)
        await _start_reading(setup)
        await _cut(setup, fault)
        await _read_settlement(setup)

        assert await _exceptions(setup) == [kind]
        states = await _line_states(setup)
        assert states.count("booked") == (1 if fault == "gross_differs" else 2)
        assert states.count("received") == states.count("waiting") == 0
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM settlement_batch WHERE booked_at IS NULL",
            )
            == 0
        )
        if fault == "net_differs":
            # The PSP paid one dollar less than its lines: booked as paid,
            # the gap stays in psp_clearing for a person.
            assert await _balance(setup, AccountKind.PSP_CLEARING) == 100

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_payment_settled_again_in_a_later_batch_is_an_exception(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        await _start_reading(setup)
        await _cut(setup)
        await _read_settlement(setup)
        in_transit = await _balance(setup, AccountKind.PAYOUT_IN_TRANSIT)

        await _cut(setup, "settled_again")
        await _read_settlement(setup)

        assert await _exceptions(setup) == ["settled_twice"]
        assert await _line_states(setup) == ["booked", "exception"]
        # Its payout is booked as the PSP paid it; the money it took from
        # psp_clearing a second time is the gap the exception names.
        paid_again = await _balance(setup, AccountKind.PAYOUT_IN_TRANSIT) - in_transit
        assert await _balance(setup, AccountKind.PSP_CLEARING) == -paid_again

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.API), body))


def test_settlement_ahead_of_our_books_waits_for_the_order(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        # A polled account: the PSP has the money before we have heard.
        await approve_at_the_psp(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state.value == "action_required"
        await _start_reading(setup)
        await _cut(setup)
        setup.clock[0] += setup.shop.account.settlement_interval
        async with await connect(setup.url, autocommit=True) as worker:
            for _ in range(20):
                for candidate in await find_candidates(worker, setup.clock[0], 50):
                    if candidate.consumer in (
                        ConsumerName.PSP_SETTLEMENT_FETCH,
                        ConsumerName.PSP_INBOX_DECODE,
                        ConsumerName.SETTLEMENT_LINE_BOOK,
                    ):
                        result = await run_one(
                            worker, setup.wired.http, ENV, candidate, setup.clock[0]
                        )
                        assert not isinstance(result, Failed), result
        assert await _line_states(setup) == ["waiting"]

        # The line asked for the payment's news: the order follows, and
        # with it the line is booked.
        await drain(setup)
        assert await session_state(setup) == "completed"
        assert await _line_states(setup) == ["booked"]
        assert await _exceptions(setup) == []
        assert await _balance(setup, AccountKind.PSP_CLEARING) == 0

    wiring = _wiring(SettlementMode.FILE, StatusMode.POLL)
    asyncio.run(with_setup(database_url, wiring, body))


def test_a_payment_settled_twice_keeps_its_first_line_whichever_runs_first(
    database_url: str,
) -> None:
    """Found by the order fuzzer (20-Nov-2026): both lines of a paid payment
    the PSP settled twice were stored, the later line's delivery ran first
    and was booked, and the first line became the exception."""

    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        assert await session_state(setup) == "completed"
        await _start_reading(setup)
        await _cut(setup)
        await _cut(setup, "settled_again")
        reading = (ConsumerName.PSP_SETTLEMENT_FETCH, ConsumerName.PSP_INBOX_DECODE)
        setup.clock[0] += setup.shop.account.settlement_interval
        async with await connect(setup.url, autocommit=True) as worker:
            for _ in range(20):
                for candidate in await find_candidates(worker, setup.clock[0], 50):
                    if candidate.consumer in reading:
                        result = await run_one(
                            worker, setup.wired.http, ENV, candidate, setup.clock[0]
                        )
                        assert not isinstance(result, Failed), result
            assert await _line_states(setup) == ["received", "received"]
            # The later line's own delivery first.
            later = [
                candidate
                for candidate in await find_candidates(worker, setup.clock[0], 50)
                if candidate.consumer == ConsumerName.SETTLEMENT_LINE_BOOK
            ][-1]
            result = await run_one(worker, setup.wired.http, ENV, later, setup.clock[0])
            assert not isinstance(result, Failed), result
        await drain(setup)

        assert await _line_states(setup) == ["booked", "exception"]
        assert await _exceptions(setup) == ["settled_twice"]

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def _report(
    batch_id: str, line_id: str, payment_id: str, reference: str, currency: str
) -> bytes:
    """A report as the fake writes one, for a line it would never cut."""
    return (
        "record,id,type,payment_id,reference,currency,gross_minor,fee_minor,"
        "net_minor,paid_out_at\n"
        f"batch,{batch_id},,,,SGD,,,0,2026-11-18T09:30:00+00:00\n"
        f"line,{line_id},capture,{payment_id},{reference},{currency},"
        "5000,0,5000,\n"
    ).encode()


# A batch's net is checked against its lines in its own currency: the USD
# line is left out of the SGD batch's sum, the SGD line is not.
@pytest.mark.parametrize(
    ("currency", "kinds"),
    [
        ("SGD", ["net_differs", "not_captured"]),
        ("USD", ["currency_differs"]),
    ],
)
def test_a_line_for_a_payment_we_did_not_capture_books_nothing(
    database_url: str, currency: str, kinds: list[str]
) -> None:
    async def body(setup: Setup) -> None:
        paid = await pay_on_page(setup)
        assert paid.status_code == 303
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "declined")
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        assert attempt.state.value == "declined"
        assert attempt.provider_payment_id is not None
        entries = await count_rows(setup.url, "SELECT count(*) FROM journal_entry")

        async with await connect(setup.url) as conn:
            await store_inbox(
                conn,
                setup.shop.account.id,
                InboundMode.SETTLEMENT_FILE,
                _report(
                    "stl_made_up",
                    "stl_made_up-1",
                    attempt.provider_payment_id,
                    str(attempt.id),
                    currency,
                ),
                setup.clock[0],
            )
            await conn.commit()
        await drain(setup)

        assert await _exceptions(setup) == kinds
        assert await _line_states(setup) == ["exception"]
        # Nothing for the line, and the made-up batch paid nothing.
        assert await count_rows(setup.url, "SELECT count(*) FROM journal_entry") == (
            entries
        )

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_line_and_a_batch_met_again_book_nothing_more(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        await _start_reading(setup)
        await _cut(setup)
        await _read_settlement(setup)
        entries = await count_rows(setup.url, "SELECT count(*) FROM journal_entry")
        async with await connect(setup.url) as conn:
            cur = await conn.execute("SELECT id, batch_id FROM settlement_line")
            row = await cur.fetchone()
            assert row is not None
            line_id, batch_id = row
            account_id = setup.shop.account.id
            again = await book_lines(
                conn,
                SettlementLineReceivedV1(
                    settlement_line_id=line_id, psp_account_id=account_id
                ),
                setup.clock[0],
            )
            assert again == Stale()
            again = await book_payout(
                conn,
                SettlementBatchReceivedV1(
                    settlement_batch_id=batch_id, psp_account_id=account_id
                ),
                setup.clock[0],
            )
            assert again == Stale()
            await conn.commit()
        assert await count_rows(setup.url, "SELECT count(*) FROM journal_entry") == (
            entries
        )

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))


def test_a_failing_read_is_tried_again_and_reads_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain(setup)
        await _start_reading(setup)
        await drain(setup)
        await _cut(setup)
        for fault in ("fail_500", "timeout_after_work"):
            response = await setup.wired.control.post(
                "http://mor/_fake/psp/faults",
                json={
                    "account": setup.shop.account.name,
                    "operation": "settlements",
                    "fault": fault,
                },
            )
            assert response.status_code == 204
        await _read_settlement(setup)
        setup.clock[0] += timedelta(minutes=30)
        await drain(setup)
        assert await _line_states(setup) == ["booked"]
        assert await _balance(setup, AccountKind.PSP_CLEARING) == 0

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.API), body))


def test_reading_starts_once_per_account(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        async with await connect(setup.url) as conn:
            account_id = setup.shop.account.id
            assert await start_settlement_reading(conn, account_id, ACTOR, NOW)
            assert not await start_settlement_reading(conn, account_id, ACTOR, NOW)
            await conn.commit()
        assert (
            await count_rows(
                setup.url,
                "SELECT count(*) FROM outbox WHERE message_type = 'psp_settlement_due'",
            )
            == 1
        )

    asyncio.run(with_setup(database_url, _wiring(SettlementMode.FILE), body))
