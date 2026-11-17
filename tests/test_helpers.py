"""The suite's own lock check, which the fuzzers' invariants rely on."""

import asyncio

import psycopg

from tests.helpers import ADMIN_URL, advisory_locks_held


def test_a_lock_held_in_another_database_is_not_counted(database_url: str) -> None:
    """Once counted the whole cluster: a lock the sandbox's worker held in
    its own database failed the soak's probe fuzzer."""

    async def body() -> None:
        async with await psycopg.AsyncConnection.connect(
            ADMIN_URL, autocommit=True
        ) as other:
            await other.execute("SELECT pg_advisory_lock(42)")
            assert await advisory_locks_held(database_url) == 0
        async with await psycopg.AsyncConnection.connect(
            database_url, autocommit=True
        ) as same:
            await same.execute("SELECT pg_advisory_lock(42)")
            assert await advisory_locks_held(database_url) == 1

    asyncio.run(body())
