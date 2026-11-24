"""One delivery: one fact for one consumer. Opening it, ending it, deferring
it, and `run_steps`, which runs an effect consumer's steps around its
vendor call. The worker picks deliveries and takes the locks; the table
(`mor.messaging.consumers`) hands each one to its consumer; both use
these, so neither imports the other's internals.

`run_steps` is the only code that holds a database connection and the
HTTP client at once: an effect's `prepare` and `complete` get the
connection, its `call` gets the client. So no row lock can be held across
a vendor call (plan-messaging-rework.md §3.3).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx2
from psycopg.rows import class_row

from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.messaging.facts import (
    AggregateType,
    Fact,
    MessageType,
    UndecodableFact,
    decode,
)
from mor.messaging.outcome import (
    Again,
    ConsumerOutcome,
    Done,
    NoCall,
    RetryLater,
    StepResult,
)
from mor.messaging.routing import ConsumerName
from mor.messaging.store import AlreadyHandled, mark_handled


MAX_BACKOFF = timedelta(hours=1)
# A bound on prepare/call/complete rounds in one run; more is a bug.
MAX_EFFECT_STEPS = 50


@dataclass(frozen=True, kw_only=True)
class Candidate:
    """A due, unhandled delivery: one fact for one consumer."""

    message_id: uuid.UUID
    consumer: ConsumerName
    aggregate_type: AggregateType
    aggregate_id: uuid.UUID


@dataclass(frozen=True)
class Ran:
    outcome: ConsumerOutcome


@dataclass(frozen=True)
class Skipped:
    """Nothing ran. `busy`: another worker holds the delivery, or the
    aggregate of an effect. `not_due`: retry time not reached.
    `already_handled`: handled since it was picked (a redelivery)."""

    reason: Literal["busy", "not_due", "already_handled"]


@dataclass(frozen=True)
class Deferred:
    """An effect got no usable answer (Unavailable, OutcomeUnknown): the
    delivery stays open until `not_before`."""

    reason: str
    not_before: datetime


@dataclass(frozen=True, kw_only=True)
class _DeliveryRow:
    not_before: datetime
    attempts: int
    handled_at: datetime | None
    message_type: MessageType
    schema_version: int
    payload: str


async def open_delivery(
    conn: Connection, candidate: Candidate, now: datetime
) -> Fact | Skipped:
    """Lock the delivery row and decode its fact, if it should run now. A
    row another worker has locked is skipped, never waited for: that worker
    is running it."""
    async with conn.cursor(row_factory=class_row(_DeliveryRow)) as cur:
        await cur.execute(
            """
            SELECT d.not_before, d.attempts, d.handled_at,
                   o.message_type, o.schema_version, o.payload::text
            FROM outbox_delivery d JOIN outbox o ON o.id = d.message_id
            WHERE d.message_id = %(message_id)s AND d.consumer = %(consumer)s
            FOR UPDATE OF d SKIP LOCKED
            """,
            {"message_id": candidate.message_id, "consumer": candidate.consumer.value},
        )
        delivery = await cur.fetchone()
    if delivery is None:
        return Skipped("busy")
    if delivery.handled_at is not None:
        return Skipped("already_handled")
    if delivery.not_before > now:
        return Skipped("not_due")
    fact = decode(
        delivery.message_type,
        delivery.schema_version,
        delivery.payload.encode("utf-8"),
    )
    if isinstance(fact, UndecodableFact):
        # We wrote it ourselves, so this is a bug.
        raise ValueError(f"undecodable fact for {candidate}: {fact}")
    return fact


async def finish_delivery(
    conn: Connection, candidate: Candidate, now: datetime
) -> None:
    """The inbox row and `handled_at`, in the transaction of the effect."""
    marked = await mark_handled(conn, candidate.consumer, candidate.message_id, now)
    if isinstance(marked, AlreadyHandled):
        # Both are written in one transaction, so one without the other is
        # a bug.
        raise AssertionError(f"inbox has {candidate} but delivery is open")
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


async def defer_delivery(
    conn: Connection, candidate: Candidate, reason: str, now: datetime
) -> datetime:
    """Leave the delivery open and set when to try again. Time only
    triggers the retry; what happens then is decided under the locks."""
    attempts = 1 + await one_value(
        conn,
        """
        SELECT attempts FROM outbox_delivery
        WHERE message_id = %(message_id)s AND consumer = %(consumer)s
        FOR UPDATE
        """,
        {"message_id": candidate.message_id, "consumer": candidate.consumer.value},
        int,
    )
    not_before = now + backoff(attempts)
    await conn.execute(
        """
        UPDATE outbox_delivery
        SET attempts = %(attempts)s, last_error = %(reason)s,
            not_before = %(not_before)s
        WHERE message_id = %(message_id)s AND consumer = %(consumer)s
        """,
        {
            "attempts": attempts,
            "reason": reason,
            "not_before": not_before,
            "message_id": candidate.message_id,
            "consumer": candidate.consumer.value,
        },
    )
    return not_before


def backoff(attempts: int) -> timedelta:
    """Wait before retry number `attempts` (1, 2, …): 2, 4, 8 … seconds,
    capped at an hour."""
    return min(timedelta(seconds=2 ** min(attempts, 20)), MAX_BACKOFF)


async def run_steps[S, C, R](
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    subject: S,
    now: datetime,
    *,
    prepare: Callable[[Connection, S, datetime], Awaitable[NoCall | C]],
    call: Callable[[httpx2.AsyncClient, Environment, C], Awaitable[R]],
    complete: Callable[[Connection, S, C, R, datetime], Awaitable[StepResult]],
) -> Ran | Deferred:
    """An effect consumer's run: `prepare` (commit), `call` with no
    transaction open, `complete` (commit), and `prepare` again while
    `complete` says `Again`. The delivery ends or is deferred in the
    transaction of the step that decides it. A crash between any two
    leaves the committed state for the next run's `prepare` to find.

    `subject` is what the fact is about (an id), narrowed by the table.
    The caller holds the aggregate's advisory lock, which keeps every
    other worker off this delivery across the commits."""
    for _ in range(MAX_EFFECT_STEPS):
        async with conn.transaction():
            prepared = await prepare(conn, subject, now)
            if isinstance(prepared, NoCall):
                await finish_delivery(conn, candidate, now)
                return Ran(prepared.outcome)
        result = await call(http, environment, prepared)
        async with conn.transaction():
            step = await complete(conn, subject, prepared, result, now)
            match step:
                case Done(outcome=outcome):
                    await finish_delivery(conn, candidate, now)
                    return Ran(outcome)
                case RetryLater(reason=reason):
                    not_before = await defer_delivery(conn, candidate, reason, now)
                    return Deferred(reason, not_before)
                case Again():
                    pass
    raise AssertionError(f"{candidate} did not finish in {MAX_EFFECT_STEPS} steps")
