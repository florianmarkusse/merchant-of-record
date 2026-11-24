"""Idempotency keys per merchant, kept for good (plan-architecture §4;
docs/plan-quote.md §5, 4d-1).

The key's row is inserted first, in the transaction that does the work,
and the response is stored on it before that transaction commits. A
concurrent request with the same key waits on the primary key until the
first commits, then finds the row: so no "in progress" state is ever
stored, and a crash rolls back the row with the work.
"""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.ids import MerchantId


@dataclass(frozen=True)
class Claimed:
    """This request owns the key: do the work, then `store_response`."""


@dataclass(frozen=True)
class Replay:
    """The key was used for this same request: answer what was answered."""

    status: int
    response: bytes


@dataclass(frozen=True)
class KeyReused:
    """The key was used for a different request."""


type Claim = Claimed | Replay | KeyReused


@dataclass(frozen=True)
class _StoredRow:
    request_sha256: bytes
    status: int | None
    response: bytes | None


async def claim_key(
    conn: Connection,
    merchant_id: MerchantId,
    key: str,
    request_sha256: bytes,
    now: datetime,
) -> Claim:
    """Insert first; a conflict means the key was used, and its row says
    for what. Never commits."""
    cur = await conn.execute(
        """
        INSERT INTO api_idempotency (merchant_id, key, request_sha256, created_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (merchant_id, key) DO NOTHING
        RETURNING key
        """,
        (merchant_id, key, request_sha256, now),
    )
    if await cur.fetchone() is not None:
        return Claimed()
    async with conn.cursor(row_factory=class_row(_StoredRow)) as cur:
        await cur.execute(
            """
            SELECT request_sha256, status, response FROM api_idempotency
            WHERE merchant_id = %s AND key = %s
            """,
            (merchant_id, key),
        )
        # The insert waited for the conflicting row to commit.
        stored = await one_row(cur, f"idempotency key {key}")
    if stored.request_sha256 != request_sha256:
        return KeyReused()
    assert stored.status is not None and stored.response is not None, (
        "a committed key always has its response: both are written in one transaction"
    )
    return Replay(stored.status, stored.response)


async def store_response(
    conn: Connection, merchant_id: MerchantId, key: str, status: int, response: bytes
) -> None:
    """The answer to replay for this key. Never commits."""
    await conn.execute(
        """
        UPDATE api_idempotency SET status = %s, response = %s
        WHERE merchant_id = %s AND key = %s AND status IS NULL
        """,
        (status, response, merchant_id, key),
    )
