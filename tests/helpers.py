import httpx2
import psycopg

from mor.config import Environment


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
