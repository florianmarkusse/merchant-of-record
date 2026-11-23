"""The daily cap on new vendor lookups a merchant's estimates start
(decided 19-Nov-2026, docs/plan-quote.md §5, 4d-1): vendors bill per call,
so what costs money is counted, not requests. The count is a row per
merchant and UTC day, read and raised under its lock in the estimate's
transaction; the day is a business rule compared under that lock."""

import uuid
from datetime import date

from mor.db.connect import Connection
from mor.db.fetch import one_value


async def take_vendor_asks(
    conn: Connection, merchant_id: uuid.UUID, day: date, count: int, limit: int
) -> bool:
    """Count `count` new lookups against the merchant's day, if that stays
    within `limit`; False, and nothing counted, if it would not. Never
    commits."""
    assert count > 0
    await conn.execute(
        """
        INSERT INTO merchant_vendor_asks (merchant_id, day, asks)
        VALUES (%s, %s, 0)
        ON CONFLICT (merchant_id, day) DO NOTHING
        """,
        (merchant_id, day),
    )
    asks = await one_value(
        conn,
        """
        SELECT asks FROM merchant_vendor_asks
        WHERE merchant_id = %s AND day = %s FOR UPDATE
        """,
        (merchant_id, day),
        int,
    )
    if asks + count > limit:
        return False
    await conn.execute(
        """
        UPDATE merchant_vendor_asks SET asks = asks + %s
        WHERE merchant_id = %s AND day = %s
        """,
        (count, merchant_id, day),
    )
    return True
