import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx2
import psycopg
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

import mor.fakes.app
import mor.web.main
from mor.config import Environment, Settings
from mor.db.connect import connect
from mor.messaging.delivery import (
    Candidate,
)
from mor.messaging.routing import (
    Effect,
    EffectConsumer,
    Local,
    LocalConsumer,
    consumer_kind,
)
from mor.messaging.worker import (
    run_one,
)


# The suite's own databases on the local cluster, reached over peer auth as
# the developer's role. Never mor_dev or mor_sandbox: these are dropped and
# recreated. The nightly soak uses its own pair (MOR_TEST_DATABASE=mor_soak),
# so a pytest run by hand never drops a database under it.
type TestDatabase = Literal["mor_test", "mor_soak"]


def _test_database(raw: str) -> TestDatabase:
    match raw:
        case "mor_test" | "mor_soak":
            return raw
        case _:
            raise ValueError(f"MOR_TEST_DATABASE must be mor_test or mor_soak: {raw!r}")


TEST_DATABASE = _test_database(os.environ.get("MOR_TEST_DATABASE", "mor_test"))
ADMIN_URL = "postgresql:///postgres"
TEST_URL = f"postgresql:///{TEST_DATABASE}"
SCRATCH_URL = f"postgresql:///{TEST_DATABASE}_migrations"


async def empty_tables(database_url: str) -> None:
    """Empty every table, ours and the fakes', but the migration records.
    The server lists the tables, so a new one is emptied without being
    named here."""
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await conn.execute(
            """
            DO $$
            DECLARE tables text;
            BEGIN
              SELECT string_agg(format('%I.%I', schemaname, tablename), ', ')
                INTO tables
              FROM pg_tables
              WHERE schemaname IN ('public', 'fake_vendor')
                AND tablename <> 'schema_migration';
              IF tables IS NOT NULL THEN
                EXECUTE 'TRUNCATE ' || tables || ' CASCADE';
              END IF;
            END $$
            """
        )
        await conn.commit()


async def advisory_locks_held(database_url: str) -> int:
    """Advisory locks held in this database by any session: a worker must
    leave none. Only this database: other databases on the cluster (the
    sandbox's worker, a soak run) take advisory locks of their own, and once
    made the soak fail by chance (17-Nov-2026)."""
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
            " AND database = (SELECT oid FROM pg_database"
            " WHERE datname = current_database())"
        )
        row = await cur.fetchone()
    assert row is not None
    count = row[0]
    assert isinstance(count, int)
    return count


ENV = Environment.TEST


def _no_outside_calls(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"unexpected outside call: {request.url}")


# For tests whose consumers never call out (the probe): any request fails.
NO_HTTP = httpx2.AsyncClient(transport=httpx2.MockTransport(_no_outside_calls))


class WorkerCrash(BaseException):
    """The worker's process dying in the middle of a call. Not an
    `Exception`, so the worker's own failure handling never sees it, as it
    would not see a killed process."""


# When the network kills the worker: before the request leaves (the vendor
# never hears of it) or after the vendor answered (its work done, the
# answer lost).
type CrashPoint = Literal["before_send", "after_answer"]


class RefusingTransport(httpx2.AsyncBaseTransport):
    """The network between us and the fakes: the fakes' app in-process,
    plus "connection refused" and a worker's crash on demand, which no app
    can produce. A fake of the network, not of our code. Inherits because
    httpx2 transports must."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.refuse_next = 0
        self.crash_next: CrashPoint | None = None

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.refuse_next > 0:
            self.refuse_next -= 1
            raise httpx2.ConnectError("connection refused", request=request)
        if self.crash_next == "before_send":
            self.crash_next = None
            raise WorkerCrash
        response = await self.inner.handle_async_request(request)
        if self.crash_next == "after_answer":
            self.crash_next = None
            raise WorkerCrash
        return response


async def time_out_at_once() -> None:
    """A fake vendor's hang (`timeout_after_work`) as the network sees it:
    the work is done and the call ends as a timeout now, not after a wall
    clock ran out (`mor.fakes.hang`). Our clients take a transport timeout
    and their own `asyncio.timeout` alike as "no answer"; the tests that
    build a fake by hand keep the real hang for the latter."""
    raise httpx2.ReadTimeout("the fake vendor hung: timed out at once")


# Every vendor account's timeout under `wire()`. No call ever needs it (a
# hang ends at once there), so it only has to be longer than the slowest
# answer: a stalled event loop once took 148 ms against 100 ms, a timeout
# the replay did not see, and the soak failed as flaky (20-Nov-2026).
VENDOR_TIMEOUT = timedelta(seconds=30)


@dataclass(kw_only=True)
class Wired:
    """Our app with the fakes mounted, all in-process. `http` is how our
    code reaches vendors (through `network`, which can refuse); the fakes'
    webhooks come back into our app through their own client."""

    app: Starlette
    http: httpx2.AsyncClient
    network: RefusingTransport
    control: httpx2.AsyncClient  # test control: bypasses the refusing network


def wire(database_url: str, clock: Callable[[], datetime]) -> Wired:
    # Our app mounts the fakes and the fakes post webhooks to our app: the
    # fakes' client forwards to an app that exists only a line later.
    built: list[Starlette] = []

    async def our_app(scope: Scope, receive: Receive, send: Send) -> None:
        await built[0](scope, receive, send)

    outgoing = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=our_app))
    fakes = mor.fakes.app.make_app(database_url, outgoing, clock, time_out_at_once)
    app = mor.web.main.make_app(
        Settings(ENV, database_url, "http://mor"),
        mor.web.main.Fakes(fakes, outgoing),
        clock,
    )
    built.append(app)
    network = RefusingTransport(httpx2.ASGITransport(app=app))
    return Wired(
        app=app,
        http=httpx2.AsyncClient(transport=network),
        network=network,
        control=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


async def crash(conn: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
    """Close a connection as a crashed worker would, and wait until its
    server process is gone. PostgreSQL frees the connection's advisory
    locks as that process exits, a moment after the client closes; a check
    right after must not see that moment. Production needs no such wait:
    a worker that meets the lock in it gets "busy" and tries later."""
    cur = await conn.execute("SELECT pg_backend_pid()")
    row = await cur.fetchone()
    assert row is not None
    pid = row[0]
    await conn.close()
    async with await psycopg.AsyncConnection.connect(
        TEST_URL, autocommit=True
    ) as probe:
        for _ in range(500):
            cur = await probe.execute(
                "SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,)
            )
            if await cur.fetchone() is None:
                return
            await asyncio.sleep(0.01)
    raise AssertionError(f"backend {pid} still alive 5 s after close")


async def crash_around_a_call(
    candidate: Candidate,
    with_call: bool,
    http: httpx2.AsyncClient,
    network: RefusingTransport,
    now: datetime,
) -> bool:
    """A worker runs the delivery and dies at its first vendor call: before
    the request is sent, or (`with_call`) after the vendor answered. Its
    committed steps stay; its advisory lock goes with its connection. True
    if it died; False if the delivery called no vendor (it ran to its end,
    or was busy, not due or handled)."""
    network.crash_next = "after_answer" if with_call else "before_send"
    crashing = await connect(TEST_URL, autocommit=True)
    try:
        try:
            await run_one(crashing, http, ENV, candidate, now)
        except WorkerCrash:
            return True
        return False
    finally:
        network.crash_next = None
        await crash(crashing)


def local_consumer(candidate: Candidate) -> LocalConsumer:
    """The candidate's consumer, which the test knows is a local one."""
    kind = consumer_kind(candidate.consumer)
    assert isinstance(kind, Local), candidate
    return kind.consumer


def effect_consumer(candidate: Candidate) -> EffectConsumer:
    """The candidate's consumer, which the test knows is an effect one."""
    kind = consumer_kind(candidate.consumer)
    assert isinstance(kind, Effect), candidate
    return kind.consumer
