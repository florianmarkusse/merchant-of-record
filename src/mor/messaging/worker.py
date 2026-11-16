"""The worker: runs due deliveries, one aggregate at a time per worker.

    uv run python -m mor.messaging.worker      # DATABASE_URL, default mor_dev

plan-architecture.md §5: pick due deliveries, take the session advisory
lock on the delivery's aggregate (try, never wait), re-read the delivery
under its row lock, run the consumer, mark it handled, release the lock.
A lock guards; time only decides when to try again (`not_before`).
"""

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from psycopg.rows import class_row

from mor.config import load_settings
from mor.dates import format_moment
from mor.db.connect import Connection, connect
from mor.messaging.consumers import handle
from mor.messaging.facts import AggregateType, MessageType, UndecodableFact, decode
from mor.messaging.outcome import ConsumerOutcome
from mor.messaging.routing import ConsumerName
from mor.messaging.store import AlreadyHandled, mark_handled


IDLE_SLEEP_SECONDS = 0.5
MAX_BACKOFF = timedelta(hours=1)


@dataclass(frozen=True)
class Candidate:
    """The oldest unhandled delivery of one consumer for one aggregate."""

    message_id: uuid.UUID
    consumer: ConsumerName
    aggregate_type: AggregateType
    aggregate_id: uuid.UUID


@dataclass(frozen=True)
class Ran:
    outcome: ConsumerOutcome


@dataclass(frozen=True)
class Skipped:
    """Nothing ran. `busy`: another worker holds the aggregate. `not_due`:
    retry time not reached. `not_next`: an older delivery comes first.
    `already_handled`: handled since it was picked (a redelivery)."""

    reason: Literal["busy", "not_due", "not_next", "already_handled"]


@dataclass(frozen=True)
class Failed:
    """The consumer raised: a bug. Rolled back and retried after a backoff;
    the aggregate's later facts wait behind it."""

    error: str
    not_before: datetime


type RunResult = Ran | Skipped | Failed


@dataclass(frozen=True)
class _CandidateRow:
    message_id: uuid.UUID
    consumer: str
    aggregate_type: str
    aggregate_id: uuid.UUID


@dataclass(frozen=True)
class _DeliveryRow:
    seq: int
    not_before: datetime
    attempts: int
    handled_at: datetime | None
    message_type: str
    schema_version: int
    payload: str


async def find_candidates(
    conn: Connection, now: datetime, limit: int
) -> list[Candidate]:
    """Per (consumer, aggregate), the oldest unhandled delivery, if due.
    Oldest first. Reading only: the advisory lock decides who runs it."""
    async with conn.cursor(row_factory=class_row(_CandidateRow)) as cur:
        await cur.execute(
            """
            SELECT message_id, consumer::text, aggregate_type::text, aggregate_id
            FROM (
              SELECT DISTINCT ON (consumer, aggregate_type, aggregate_id)
                message_id, consumer, aggregate_type, aggregate_id, seq, not_before
              FROM outbox_delivery
              WHERE handled_at IS NULL
              ORDER BY consumer, aggregate_type, aggregate_id, seq
            ) AS oldest
            WHERE not_before <= %(now)s
            ORDER BY seq
            LIMIT %(limit)s
            """,
            {"now": now, "limit": limit},
        )
        rows = await cur.fetchall()
    candidates: list[Candidate] = []
    for row in rows:
        candidates.append(
            Candidate(
                row.message_id,
                ConsumerName(row.consumer),
                AggregateType(row.aggregate_type),
                row.aggregate_id,
            )
        )
    return candidates


def _lock_name(candidate: Candidate) -> str:
    return f"{candidate.aggregate_type.value}:{candidate.aggregate_id}"


async def try_lock_aggregate(conn: Connection, candidate: Candidate) -> bool:
    """The session advisory lock on the aggregate: held across commits and
    freed when the connection dies. A 64-bit hash of the name; a collision
    only makes two aggregates wait for each other."""
    cur = await conn.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        (_lock_name(candidate),),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0] is True


async def unlock_aggregate(conn: Connection, candidate: Candidate) -> None:
    cur = await conn.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
        (_lock_name(candidate),),
    )
    row = await cur.fetchone()
    assert row is not None and row[0] is True, "unlocked a lock not held"


async def handle_delivery(
    conn: Connection, candidate: Candidate, now: datetime
) -> Ran | Skipped:
    """One delivery, in the caller's transaction, under the aggregate's
    advisory lock. Never commits."""
    async with conn.cursor(row_factory=class_row(_DeliveryRow)) as cur:
        await cur.execute(
            """
            SELECT d.seq, d.not_before, d.attempts, d.handled_at,
                   o.message_type::text, o.schema_version, o.payload::text
            FROM outbox_delivery d JOIN outbox o ON o.id = d.message_id
            WHERE d.message_id = %(message_id)s AND d.consumer = %(consumer)s
            FOR UPDATE OF d
            """,
            {"message_id": candidate.message_id, "consumer": candidate.consumer.value},
        )
        delivery = await cur.fetchone()
    assert delivery is not None, f"no delivery for {candidate}"
    if delivery.handled_at is not None:
        return Skipped("already_handled")
    if delivery.not_before > now:
        return Skipped("not_due")
    cur = await conn.execute(
        """
        SELECT 1 FROM outbox_delivery
        WHERE consumer = %(consumer)s AND aggregate_type = %(aggregate_type)s
          AND aggregate_id = %(aggregate_id)s AND handled_at IS NULL
          AND seq < %(seq)s
        LIMIT 1
        """,
        {
            "consumer": candidate.consumer.value,
            "aggregate_type": candidate.aggregate_type.value,
            "aggregate_id": candidate.aggregate_id,
            "seq": delivery.seq,
        },
    )
    if await cur.fetchone() is not None:
        return Skipped("not_next")

    marked = await mark_handled(conn, candidate.consumer, candidate.message_id, now)
    if isinstance(marked, AlreadyHandled):
        # The inbox row and handled_at are written in one transaction, so
        # one without the other is a bug.
        raise AssertionError(f"inbox has {candidate} but delivery is open")

    fact = decode(
        MessageType(delivery.message_type),
        delivery.schema_version,
        delivery.payload.encode("utf-8"),
    )
    if isinstance(fact, UndecodableFact):
        # We wrote it ourselves, so this is a bug.
        raise ValueError(f"undecodable fact for {candidate}: {fact}")
    outcome = await handle(conn, candidate.consumer, fact, now)
    await conn.execute(
        """
        UPDATE outbox_delivery SET handled_at = %(now)s
        WHERE message_id = %(message_id)s AND consumer = %(consumer)s
        """,
        {
            "now": now,
            "message_id": candidate.message_id,
            "consumer": candidate.consumer.value,
        },
    )
    return Ran(outcome)


def backoff(attempts: int) -> timedelta:
    """Wait before retry number `attempts` (1, 2, …): 2, 4, 8 … seconds,
    capped at an hour."""
    return min(timedelta(seconds=2 ** min(attempts, 20)), MAX_BACKOFF)


async def run_one(conn: Connection, candidate: Candidate, now: datetime) -> RunResult:
    """Lock the aggregate, run one delivery in its own transaction, unlock.
    `conn` is the worker's dedicated autocommit connection."""
    if not await try_lock_aggregate(conn, candidate):
        return Skipped("busy")
    try:
        try:
            async with conn.transaction():
                return await handle_delivery(conn, candidate, now)
        except Exception as error:
            return await _record_failure(conn, candidate, repr(error), now)
    finally:
        await unlock_aggregate(conn, candidate)


async def _record_failure(
    conn: Connection, candidate: Candidate, error: str, now: datetime
) -> Failed:
    async with conn.transaction():
        cur = await conn.execute(
            """
            SELECT attempts FROM outbox_delivery
            WHERE message_id = %(message_id)s AND consumer = %(consumer)s
            FOR UPDATE
            """,
            {"message_id": candidate.message_id, "consumer": candidate.consumer.value},
        )
        row = await cur.fetchone()
        assert row is not None
        attempts = row[0] + 1
        not_before = now + backoff(attempts)
        await conn.execute(
            """
            UPDATE outbox_delivery
            SET attempts = %(attempts)s, last_error = %(error)s,
                not_before = %(not_before)s
            WHERE message_id = %(message_id)s AND consumer = %(consumer)s
            """,
            {
                "attempts": attempts,
                "error": error,
                "not_before": not_before,
                "message_id": candidate.message_id,
                "consumer": candidate.consumer.value,
            },
        )
    return Failed(error, not_before)


async def drain_once(
    conn: Connection, now: datetime, limit: int = 100
) -> list[RunResult]:
    """Try each due candidate once. Returns what happened to each."""
    results: list[RunResult] = []
    for candidate in await find_candidates(conn, now, limit):
        results.append(await run_one(conn, candidate, now))
    return results


async def run_worker(database_url: str) -> None:
    async with await connect(database_url, autocommit=True) as conn:
        while True:
            results = await drain_once(conn, datetime.now(UTC))
            for result in results:
                if isinstance(result, Failed):
                    retry_at = format_moment(result.not_before)
                    print(f"failed: {result.error}; retry at {retry_at}")
            ran = False
            for result in results:
                if isinstance(result, Ran):
                    ran = True
            if not ran:
                await asyncio.sleep(IDLE_SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_worker(load_settings(os.environ).database_url))
