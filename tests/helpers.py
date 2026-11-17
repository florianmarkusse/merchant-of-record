import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx2
import psycopg
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

import mor.fakes.echo
import mor.web.main
from mor.config import Environment, Settings


# The suite's own databases on the local cluster, reached over peer auth as
# the developer's role. Never mor_dev: these are dropped and recreated.
ADMIN_URL = "postgresql:///postgres"
TEST_URL = "postgresql:///mor_test"
SCRATCH_URL = "postgresql:///mor_test_migrations"


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
    """Advisory locks held by any session: a worker must leave none."""
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
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


class RefusingTransport(httpx2.AsyncBaseTransport):
    """The network between us and the fakes: the fakes' app in-process,
    plus "connection refused" on demand, which no app can produce. A fake of
    the network, not of our code. Inherits because httpx2 transports must."""

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner
        self.refuse_next = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.refuse_next > 0:
            self.refuse_next -= 1
            raise httpx2.ConnectError("connection refused", request=request)
        return await self.inner.handle_async_request(request)


@dataclass
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
    fakes = mor.fakes.echo.make_app(database_url, outgoing)
    app = mor.web.main.make_app(Settings(ENV, database_url), fakes, clock)
    built.append(app)
    network = RefusingTransport(httpx2.ASGITransport(app=app))
    return Wired(
        app,
        httpx2.AsyncClient(transport=network),
        network,
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
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
