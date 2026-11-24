"""Layer 3 (plan-architecture.md §10): real workers on their own connections
against the same rows, at once (probes, echo jobs, invoice numbers, a
webhook racing a capture). Not shrunk; it checks that the locks do what the
serial fuzzer assumes."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx2

import mor.payments.flow
from mor.audit import Actor, ActorKind
from mor.db.connect import connect
from mor.echo.flow import create_echo_account, create_echo_job
from mor.echo.port import EchoProvider
from mor.fakes.echo import make_app
from mor.fakes.hang import hang_a_minute
from mor.messaging.delivery import (
    Ran,
    Skipped,
    finish_delivery,
)
from mor.messaging.guard import DuplicateGuard
from mor.messaging.outcome import Done
from mor.messaging.routing import ConsumerName
from mor.messaging.worker import (
    Failed,
    RunResult,
    drain_once,
    find_candidates,
    run_one,
    try_lock_aggregate,
    unlock_aggregate,
)
from mor.payments.flow import CaptureCall
from mor.payments.transitions import AttemptState
from mor.probe.flow import start_probe
from mor.psp.port import CaptureMode, StatusMode
from tests.helpers import ENV, NO_HTTP, advisory_locks_held, count_rows
from tests.payments.support import (
    FAT_SALE_KEYED,
    SGD_PRICE,
    Setup,
    Wiring,
    approve_at_the_psp,
    attempts_of,
    drain,
    drain_all_but_invoices,
    pay_on_page,
    quoted_session,
    release,
    with_setup,
)


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
PROBES = 200
WORKERS = 6


async def _worker(url: str, results: list[RunResult]) -> None:
    async with await connect(url, autocommit=True) as conn:
        idle_rounds = 0
        while idle_rounds < 3:
            round_results = await drain_once(conn, NO_HTTP, ENV, NOW, limit=20)
            results.extend(round_results)
            ran = False
            for result in round_results:
                if isinstance(result, Ran):
                    ran = True
            idle_rounds = 0 if ran else idle_rounds + 1
            await asyncio.sleep(0)


def test_many_workers_finish_every_probe_once(database_url: str) -> None:
    async def scenario() -> list[RunResult]:
        async with await connect(database_url) as conn:
            for _ in range(PROBES):
                await start_probe(conn, Actor(ActorKind.SYSTEM, "test"), NOW)
            await conn.commit()
        results: list[RunResult] = []
        await asyncio.gather(*[_worker(database_url, results) for _ in range(WORKERS)])
        return results

    results = asyncio.run(scenario())

    ran = 0
    for result in results:
        assert not isinstance(result, Failed), result
        if isinstance(result, Ran):
            ran += 1
        else:
            assert isinstance(result, Skipped)
    # Each probe: one echo and one finish ran, whatever the interleaving.
    assert ran == 2 * PROBES

    async def checks() -> None:
        finished = "SELECT count(*) FROM probe WHERE state = 'finished'"
        assert await count_rows(database_url, finished) == PROBES
        echoed = "SELECT count(*) FROM outbox WHERE message_type = 'probe_echoed'"
        assert await count_rows(database_url, echoed) == PROBES
        inbox = "SELECT count(*) FROM inbox"
        assert await count_rows(database_url, inbox) == 2 * PROBES
        open_deliveries = (
            "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        assert await count_rows(database_url, open_deliveries) == 0
        assert await advisory_locks_held(database_url) == 0

    asyncio.run(checks())


ECHO_JOBS_PER_PROVIDER = 30


async def _echo_worker(
    url: str, http: httpx2.AsyncClient, results: list[RunResult]
) -> None:
    async with await connect(url, autocommit=True) as conn:
        idle_rounds = 0
        while idle_rounds < 3:
            round_results = await drain_once(conn, http, ENV, NOW, limit=20)
            results.extend(round_results)
            ran = False
            for result in round_results:
                if isinstance(result, Ran):
                    ran = True
            idle_rounds = 0 if ran else idle_rounds + 1
            await asyncio.sleep(0)


def test_many_workers_send_every_echo_once_across_the_call(database_url: str) -> None:
    # The advisory lock is held across the vendor call, between two commits.
    # Without it a second worker sees a `sent` request with no answer and,
    # on `plain`, hands a job that is being sent to a person.
    async def scenario() -> list[RunResult]:
        async with await connect(database_url) as conn:
            for provider in EchoProvider:
                flavour = provider.value.removeprefix("fake_")
                account_id = await create_echo_account(
                    conn,
                    provider,
                    f"http://fake/_fake/echo/{flavour}/acct",
                    timedelta(seconds=5),
                )
                for _ in range(ECHO_JOBS_PER_PROVIDER):
                    await create_echo_job(
                        conn, account_id, "hello", Actor(ActorKind.SYSTEM, "test"), NOW
                    )
            await conn.commit()
        results: list[RunResult] = []
        transport = httpx2.ASGITransport(
            app=make_app(database_url, NO_HTTP, hang_a_minute)
        )
        async with httpx2.AsyncClient(transport=transport) as http:
            await asyncio.gather(
                *[_echo_worker(database_url, http, results) for _ in range(WORKERS)]
            )
        return results

    results = asyncio.run(scenario())

    for result in results:
        assert not isinstance(result, Failed), result

    async def checks() -> None:
        jobs = 3 * ECHO_JOBS_PER_PROVIDER
        echoed = "SELECT count(*) FROM echo_job WHERE state = 'echoed'"
        assert await count_rows(database_url, echoed) == jobs
        made = "SELECT count(*) FROM fake_vendor.echo"
        assert await count_rows(database_url, made) == jobs
        assert await advisory_locks_held(database_url) == 0

    asyncio.run(checks())


INVOICED_ORDERS = 24


async def _invoice_worker(url: str, now: datetime, results: list[RunResult]) -> None:
    async with await connect(url, autocommit=True) as conn:
        idle_rounds = 0
        while idle_rounds < 3:
            round_results = await drain_once(conn, NO_HTTP, ENV, now, limit=5)
            results.extend(round_results)
            ran = False
            for result in round_results:
                if isinstance(result, Ran):
                    ran = True
            idle_rounds = 0 if ran else idle_rounds + 1
            await asyncio.sleep(0)


def test_many_workers_number_invoices_without_a_gap(database_url: str) -> None:
    """Paid orders waiting for their invoices, issued by workers at once:
    the series row's lock hands out 1…n, each once."""

    async def body(setup: Setup) -> None:
        await approve_at_the_psp(setup)
        await drain_all_but_invoices(setup)
        for _ in range(INVOICED_ORDERS - 1):
            session_id, option = await quoted_session(
                setup.url, setup.shop.merchant_id, SGD_PRICE, setup.clock[0]
            )
            paid = Setup(
                url=setup.url,
                wired=setup.wired,
                clock=setup.clock,
                shop=setup.shop,
                session_id=session_id,
                option=option,
            )
            await approve_at_the_psp(paid)
            await drain_all_but_invoices(paid)
        assert await count_rows(setup.url, "SELECT count(*) FROM invoice") == 0
        results: list[RunResult] = []
        await asyncio.gather(
            *[
                _invoice_worker(setup.url, setup.clock[0], results)
                for _ in range(WORKERS)
            ]
        )
        for result in results:
            assert not isinstance(result, Failed), result
        async with await connect(setup.url) as conn:
            cur = await conn.execute("SELECT number FROM invoice ORDER BY number")
            numbers = [row[0] for row in await cur.fetchall()]
            cur = await conn.execute("SELECT next_number FROM invoice_series")
            counters = await cur.fetchall()
        assert numbers == list(range(1, INVOICED_ORDERS + 1))
        assert counters == [(INVOICED_ORDERS + 1,)]
        assert await advisory_locks_held(setup.url) == 0

    asyncio.run(with_setup(database_url, FAT_SALE_KEYED, body))


def test_a_webhook_is_applied_while_the_capture_call_is_out(
    database_url: str,
) -> None:
    """A local consumer does not wait for an effect's advisory lock
    (plan-messaging-rework.md §3.2): the PSP's `captured` webhook is applied
    while our capture call is still out, and the capture's `complete` then
    finds the attempt captured and changes nothing more."""
    wiring = Wiring(
        status_mode=StatusMode.PUSH_FAT,
        capture_mode=CaptureMode.AUTHORISE_CAPTURE,
        guard=DuplicateGuard.VENDOR_CONSTRAINT,
    )

    async def body(setup: Setup) -> None:
        await pay_on_page(setup)
        await drain(setup)
        (attempt,) = await attempts_of(setup)
        await release(setup, attempt, "paid")
        now = setup.clock[0]
        other = await connect(setup.url, autocommit=True)
        # Everything up to the capture: the authorised webhook.
        capture = None
        for _ in range(20):
            for found in await find_candidates(other, now, 50):
                if found.consumer == ConsumerName.PSP_PAYMENT_CAPTURE:
                    capture = found
                else:
                    result = await run_one(other, setup.wired.http, ENV, found, now)
                    assert not isinstance(result, Failed), result
            if capture is not None:
                break
        assert capture is not None
        capturing = await connect(setup.url, autocommit=True)
        try:
            async with other:
                # The capture: prepared, the call made (the fake pushes the
                # `captured` webhook during it), no `complete` yet.
                assert await try_lock_aggregate(capturing, capture)
                attempt_id = capture.aggregate_id
                async with capturing.transaction():
                    prepared = await mor.payments.flow.prepare_capture(
                        capturing, attempt_id, now
                    )
                assert isinstance(prepared, CaptureCall)
                answer = await mor.payments.flow.send_capture(
                    setup.wired.http, ENV, prepared
                )

                # Meanwhile another worker applies the webhook on the same
                # attempt, which used to wait as "busy".
                applied = False
                for _ in range(10):
                    for found in await find_candidates(other, now, 50):
                        if found == capture:
                            continue
                        result = await run_one(other, setup.wired.http, ENV, found, now)
                        assert not isinstance(result, Failed), result
                        if found.consumer == ConsumerName.PAYMENT_EVENT_APPLY:
                            assert isinstance(result, Ran), result
                            applied = True
                assert applied
                (attempt,) = await attempts_of(setup)
                assert attempt.state == AttemptState.CAPTURED

            async with capturing.transaction():
                completed = await mor.payments.flow.complete_capture(
                    capturing, attempt_id, prepared, answer, now
                )
                assert isinstance(completed, Done), completed
                await finish_delivery(capturing, capture, now)
        finally:
            await unlock_aggregate(capturing, capture)
            await capturing.close()
        await drain(setup)

        (attempt,) = await attempts_of(setup)
        assert attempt.state == AttemptState.CAPTURED
        charges = "SELECT count(*) FROM fake_vendor.psp_fake_charge"
        assert await count_rows(setup.url, charges) == 1
        assert await count_rows(setup.url, "SELECT count(*) FROM order_") == 1
        assert await count_rows(setup.url, "SELECT count(*) FROM journal_entry") == 1
        # Nothing due is left open (the session's expiry is due tomorrow).
        async with await connect(setup.url) as conn:
            cur = await conn.execute(
                "SELECT consumer FROM outbox_delivery"
                " WHERE handled_at IS NULL AND not_before <= %s",
                (setup.clock[0],),
            )
            assert await cur.fetchall() == []
        assert await advisory_locks_held(setup.url) == 0

    asyncio.run(with_setup(database_url, wiring, body))
