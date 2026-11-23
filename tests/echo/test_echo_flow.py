"""The echo flow through the worker, against the fake echo vendor in-process:
each result kind, and each duplicate guard after a lost answer or a crash
between commits (docs/plan-messaging.md §10)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection, connect
from mor.echo.flow import (
    create_echo_account,
    create_echo_job,
    echo_job_state,
)
from mor.echo.port import EchoProvider
from mor.echo.records import EchoJobState
from mor.fakes.echo import make_app
from mor.fakes.hang import hang_a_minute
from mor.messaging.outcome import Applied, Stale
from mor.messaging.worker import (
    Deferred,
    Failed,
    Prepared,
    Ran,
    drain_once,
    find_candidates,
    prepare_delivery,
    try_lock_aggregate,
)
from tests.helpers import ENV, NO_HTTP, RefusingTransport, crash, effect_consumer


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
LATER = NOW + timedelta(seconds=3)  # past the first retry's backoff
ACTOR = Actor(ActorKind.SYSTEM, "test")
FLAVOUR = {
    EchoProvider.FAKE_KEYED: "keyed",
    EchoProvider.FAKE_CANCELLABLE: "cancellable",
    EchoProvider.FAKE_PLAIN: "plain",
}


@dataclass
class Setup:
    url: str
    http: httpx2.AsyncClient
    network: RefusingTransport
    worker: Connection


async def _with_setup(url: str, body: Callable[[Setup], Awaitable[None]]) -> None:
    network = RefusingTransport(
        httpx2.ASGITransport(app=make_app(url, NO_HTTP, hang_a_minute))
    )
    async with (
        httpx2.AsyncClient(transport=network) as http,
        await connect(url, autocommit=True) as worker,
    ):
        await body(Setup(url, http, network, worker))


async def _job(setup: Setup, provider: EchoProvider) -> uuid.UUID:
    async with await connect(setup.url) as conn:
        account_id = await create_echo_account(
            conn,
            provider,
            f"http://fake/_fake/echo/{FLAVOUR[provider]}/acct",
            timedelta(milliseconds=200),
        )
        job_id = await create_echo_job(conn, account_id, "hello", ACTOR, NOW)
        await conn.commit()
    return job_id


async def _fault(setup: Setup, provider: EchoProvider, fault: str) -> None:
    response = await setup.http.post(
        "http://fake/_fake/echo/faults",
        json={"flavour": FLAVOUR[provider], "fault": fault},
    )
    assert response.status_code == 204


async def _state(setup: Setup, job_id: uuid.UUID) -> EchoJobState:
    async with await connect(setup.url) as conn:
        return await echo_job_state(conn, job_id)


async def _vendor_echoes(setup: Setup, job_id: uuid.UUID) -> list[str]:
    """What the vendor holds for the job: each echo's state, oldest first."""
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT state::text FROM fake_vendor.echo WHERE reference = %s"
            " ORDER BY created_at, id",
            (str(job_id),),
        )
        rows = await cur.fetchall()
    states: list[str] = []
    for (state,) in rows:
        states.append(state)
    return states


async def _facts(setup: Setup, job_id: uuid.UUID) -> list[str]:
    async with await connect(setup.url) as conn:
        cur = await conn.execute(
            "SELECT message_type::text FROM outbox WHERE aggregate_id = %s"
            " ORDER BY seq",
            (job_id,),
        )
        rows = await cur.fetchall()
    types: list[str] = []
    for (message_type,) in rows:
        types.append(message_type)
    return types


async def _drain(setup: Setup, now: datetime) -> list[object]:
    return list(await drain_once(setup.worker, setup.http, ENV, now))


async def _crash_after_prepare(setup: Setup) -> None:
    """A worker that commits `prepare` and dies before calling."""
    crashing = await connect(setup.url, autocommit=True)
    candidates = await find_candidates(crashing, NOW, 10)
    assert len(candidates) == 1
    assert await try_lock_aggregate(crashing, candidates[0])
    async with crashing.transaction():
        prepared = await prepare_delivery(
            crashing, candidates[0], effect_consumer(candidates[0]), NOW
        )
    assert isinstance(prepared, Prepared)
    await crash(crashing)


def test_each_provider_echoes_a_job_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        for provider in EchoProvider:
            job_id = await _job(setup, provider)
            assert await _drain(setup, NOW) == [Ran(Applied())]
            assert await _state(setup, job_id) == EchoJobState.ECHOED
            assert await _vendor_echoes(setup, job_id) == ["live"]
            assert await _facts(setup, job_id) == [
                "echo_job_requested",
                "echo_job_echoed",
            ]
            # echo_job_echoed starts tracking; a `none` account polls nothing.
            assert await _drain(setup, NOW) == [Ran(Stale())]

    asyncio.run(_with_setup(database_url, body))


def test_a_refusal_ends_the_job_refused(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_PLAIN)
        await _fault(setup, EchoProvider.FAKE_PLAIN, "refuse")
        assert await _drain(setup, NOW) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.REFUSED
        assert await _vendor_echoes(setup, job_id) == []

    asyncio.run(_with_setup(database_url, body))


def test_a_refused_connection_is_sent_again_later_on_any_guard(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_PLAIN)
        setup.network.refuse_next = 1
        results = await _drain(setup, NOW)
        assert len(results) == 1 and isinstance(results[0], Deferred)
        # Even with guard `none`: a refused connection never reached it.
        assert await _drain(setup, LATER) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        assert await _vendor_echoes(setup, job_id) == ["live"]

    asyncio.run(_with_setup(database_url, body))


def test_keyed_lost_answer_is_resent_with_the_same_key(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_KEYED)
        await _fault(setup, EchoProvider.FAKE_KEYED, "timeout_after_work")
        results = await _drain(setup, NOW)
        assert isinstance(results[0], Deferred)
        assert await _drain(setup, LATER) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        assert await _vendor_echoes(setup, job_id) == ["live"]

    asyncio.run(_with_setup(database_url, body))


def test_cancellable_lost_answer_is_resent_and_the_duplicate_cancelled(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_CANCELLABLE)
        await _fault(setup, EchoProvider.FAKE_CANCELLABLE, "timeout_after_work")
        assert isinstance((await _drain(setup, NOW))[0], Deferred)
        assert await _drain(setup, LATER) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        # The resend made a second echo; the first is cancelled, the one
        # whose answer we got is kept.
        assert await _vendor_echoes(setup, job_id) == ["cancelled", "live"]

    asyncio.run(_with_setup(database_url, body))


def test_cancellable_resend_refused_keeps_the_echo_the_first_send_made(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_CANCELLABLE)
        await _fault(setup, EchoProvider.FAKE_CANCELLABLE, "timeout_after_work")
        await _fault(setup, EchoProvider.FAKE_CANCELLABLE, "refuse")
        assert isinstance((await _drain(setup, NOW))[0], Deferred)
        assert await _drain(setup, LATER) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        assert await _vendor_echoes(setup, job_id) == ["live"]

    asyncio.run(_with_setup(database_url, body))


def test_plain_lost_answer_is_looked_up_never_resent(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_PLAIN)
        await _fault(setup, EchoProvider.FAKE_PLAIN, "timeout_after_work")
        assert isinstance((await _drain(setup, NOW))[0], Deferred)
        assert await _drain(setup, LATER) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        assert await _vendor_echoes(setup, job_id) == ["live"]

    asyncio.run(_with_setup(database_url, body))


def test_plain_send_that_may_have_left_but_is_unknown_goes_to_a_person(
    database_url: str,
) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_PLAIN)
        await _crash_after_prepare(setup)
        # Marked sent, never answered, and the vendor does not know it: a
        # lookup cannot prove it never left, so nothing is resent.
        assert await _drain(setup, NOW) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.NEEDS_PERSON
        assert await _vendor_echoes(setup, job_id) == []
        assert (await _facts(setup, job_id))[-1] == "echo_job_needs_person"

    asyncio.run(_with_setup(database_url, body))


def test_keyed_crash_after_prepare_sends_once(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_KEYED)
        await _crash_after_prepare(setup)
        assert await _drain(setup, NOW) == [Ran(Applied())]
        assert await _state(setup, job_id) == EchoJobState.ECHOED
        assert await _vendor_echoes(setup, job_id) == ["live"]

    asyncio.run(_with_setup(database_url, body))


def test_a_fake_provider_is_refused_in_prod(database_url: str) -> None:
    async def body(setup: Setup) -> None:
        job_id = await _job(setup, EchoProvider.FAKE_KEYED)
        results = await drain_once(setup.worker, setup.http, Environment.PROD, NOW)
        assert len(results) == 1 and isinstance(results[0], Failed)
        assert "AccountNotAllowed" in results[0].error
        assert await _vendor_echoes(setup, job_id) == []
        assert await _state(setup, job_id) == EchoJobState.REQUESTED

    asyncio.run(_with_setup(database_url, body))
