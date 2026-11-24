"""What every connection reads differently from psycopg's defaults: moments
in UTC, whatever the server's own time zone (this laptop's cluster runs in
Europe/Amsterdam), and `numeric` as a whole `int`, never `Decimal`."""

import asyncio
from datetime import UTC, datetime

import pytest

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


def test_a_numeric_beyond_bigint_reads_as_a_whole_int(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT (10::numeric ^ 30)::numeric(40, 0), -12::numeric"
            )
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 10**30
        assert type(row[0]) is int
        assert row[1] == -12

    asyncio.run(body())


def test_a_fractional_numeric_is_refused_on_reading(database_url: str) -> None:
    async def body() -> None:
        async with await connect(database_url) as conn:
            cur = await conn.execute("SELECT 1.5::numeric")
            with pytest.raises(ValueError, match="not a whole number"):
                await cur.fetchone()

    asyncio.run(body())
