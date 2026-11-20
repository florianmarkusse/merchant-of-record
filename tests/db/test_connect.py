"""Moments come back from the database in UTC, whatever the server's own
time zone (this laptop's cluster runs in Europe/Amsterdam)."""

import asyncio
from datetime import UTC, datetime

from mor.db.connect import connect


def test_a_moment_read_back_is_in_utc(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            cur = await conn.execute("SELECT '2026-11-18 16:30:00+07'::timestamptz")
            row = await cur.fetchone()
        assert row is not None
        moment = row[0]
        assert isinstance(moment, datetime)
        assert moment.tzinfo == UTC
        assert moment == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)

    asyncio.run(body())
