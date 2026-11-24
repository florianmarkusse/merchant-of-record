"""Orders in the database (docs/plan-payments.md §5, §6.1). Persistence
functions take the connection first and never commit."""

from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from psycopg.rows import class_row

from mor.db.connect import Connection
from mor.ids import (
    CheckoutAddressId,
    CheckoutSessionId,
    MerchantId,
    OrderId,
    PaymentAttemptId,
    QuoteId,
)


@dataclass(frozen=True, kw_only=True)
class Order:
    id: OrderId
    session_id: CheckoutSessionId
    attempt_id: PaymentAttemptId
    merchant_id: MerchantId
    quote_id: QuoteId
    option_no: int
    address_id: CheckoutAddressId
    paid_at: datetime
    buyer_token: str  # the order page's link: a bearer token


# A row read under its row lock (`SELECT … FOR UPDATE`), made only by
# the lock function: the functions that change the row take this type, so
# pyright refuses a read-then-write on a row read without its lock
# (docs/plan-messaging.md §5). It says the row was locked when
# read, not that the transaction holding the lock is still open.
LockedOrder = NewType("LockedOrder", Order)


async def order_by_token(conn: Connection, buyer_token: str) -> Order | None:
    async with conn.cursor(row_factory=class_row(Order)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, attempt_id, merchant_id, quote_id, option_no,
                   address_id, paid_at, buyer_token
            FROM order_ WHERE buyer_token = %s
            """,
            (buyer_token,),
        )
        return await cur.fetchone()


async def order_by_session(
    conn: Connection, session_id: CheckoutSessionId
) -> Order | None:
    async with conn.cursor(row_factory=class_row(Order)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, attempt_id, merchant_id, quote_id, option_no,
                   address_id, paid_at, buyer_token
            FROM order_ WHERE session_id = %s
            """,
            (session_id,),
        )
        return await cur.fetchone()


async def read_order(
    conn: Connection, merchant_id: MerchantId, order_id: OrderId
) -> Order | None:
    """The merchant's own order; another merchant's reads as none."""
    async with conn.cursor(row_factory=class_row(Order)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, attempt_id, merchant_id, quote_id, option_no,
                   address_id, paid_at, buyer_token
            FROM order_ WHERE id = %s AND merchant_id = %s
            """,
            (order_id, merchant_id),
        )
        return await cur.fetchone()


async def lock_order(conn: Connection, order_id: OrderId) -> LockedOrder | None:
    """The order under its row lock, for a step that adds to it."""
    async with conn.cursor(row_factory=class_row(Order)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, attempt_id, merchant_id, quote_id, option_no,
                   address_id, paid_at, buyer_token
            FROM order_ WHERE id = %s FOR UPDATE
            """,
            (order_id,),
        )
        found = await cur.fetchone()
    return None if found is None else LockedOrder(found)
