"""Idempotency keys per merchant, kept for good (plan-architecture §4;
docs/plan-quote.md §5, 4d-1).

The key's row is inserted first, in the transaction that does the work,
and the response is stored on it before that transaction commits. A
concurrent request with the same key waits on the primary key until the
first commits, then finds the row: so no "in progress" state is ever
stored, and a crash rolls back the row with the work.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from mor.db.connect import Connection


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


async def claim_key(
    conn: Connection,
    merchant_id: uuid.UUID,
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
    cur = await conn.execute(
        """
        SELECT request_sha256, status, response FROM api_idempotency
        WHERE merchant_id = %s AND key = %s
        """,
        (merchant_id, key),
    )
    row = await cur.fetchone()
    assert row is not None, "the conflicting row is committed: it waited for it"
    stored_sha256, status, response = row
    if bytes(stored_sha256) != request_sha256:
        return KeyReused()
    assert status is not None and response is not None, (
        "a committed key always has its response: both are written in one transaction"
    )
    return Replay(status, bytes(response))


async def store_response(
    conn: Connection, merchant_id: uuid.UUID, key: str, status: int, response: bytes
) -> None:
    """The answer to replay for this key. Never commits."""
    await conn.execute(
        """
        UPDATE api_idempotency SET status = %s, response = %s
        WHERE merchant_id = %s AND key = %s AND status IS NULL
        """,
        (status, response, merchant_id, key),
    )
