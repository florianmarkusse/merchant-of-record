"""The worker: runs due deliveries, one aggregate at a time per worker.

    uv run python -m mor.messaging.worker      # DATABASE_URL, default mor_dev

plan-architecture.md §5: pick due deliveries, take the session advisory
lock on the delivery's aggregate (try, never wait), re-read the delivery
under its row lock, run the consumer, mark it handled, release the lock.
A lock guards; time only decides when to try again (`not_before`).
"""

import asyncio
import os
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, assert_never

import httpx2
import watchfiles
from psycopg.rows import class_row
from watchfiles import PythonFilter

from mor.config import Environment, Settings, load_settings
from mor.dates import format_moment
from mor.db.connect import Connection, connect
from mor.messaging import consumers
from mor.messaging.consumers import EffectAnswer, EffectCall
from mor.messaging.facts import (
    AggregateType,
    Fact,
    MessageType,
    UndecodableFact,
    decode,
)
from mor.messaging.outcome import Again, ConsumerOutcome, Done, NoCall, RetryLater
from mor.messaging.routing import (
    ConsumerName,
    Effect,
    EffectConsumer,
    Local,
    LocalConsumer,
    consumer_kind,
)
from mor.messaging.store import AlreadyHandled, mark_handled


# An idle worker waits for a NOTIFY on channel `outbox_delivery`
# (0003_delivery_notify.sql), for the next retry to come due, or at most this
# long: notifications sent while no worker listens are not kept, so polling
# stays as the fallback.
FALLBACK_POLL = timedelta(seconds=5)
MAX_BACKOFF = timedelta(hours=1)
# A bound on prepare/call/complete rounds in one run; more is a bug.
MAX_EFFECT_STEPS = 50
SOURCE = Path(__file__).resolve().parent.parent.parent  # src/, watched outside prod


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


@dataclass(frozen=True)
class Deferred:
    """An effect got no usable answer (Unavailable, OutcomeUnknown): the
    delivery stays open until `not_before`."""

    reason: str
    not_before: datetime


type RunResult = Ran | Skipped | Deferred | Failed


@dataclass(frozen=True)
class _DeliveryRow:
    seq: int
    not_before: datetime
    attempts: int
    handled_at: datetime | None
    message_type: MessageType
    schema_version: int
    payload: str


async def find_candidates(
    conn: Connection, now: datetime, limit: int
) -> list[Candidate]:
    """Per (consumer, aggregate), the oldest unhandled delivery, if due.
    Oldest first. Reading only: the advisory lock decides who runs it."""
    async with conn.cursor(row_factory=class_row(Candidate)) as cur:
        await cur.execute(
            """
            SELECT message_id, consumer, aggregate_type, aggregate_id
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
        return await cur.fetchall()


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


async def _open_delivery(
    conn: Connection, candidate: Candidate, now: datetime
) -> Fact | Skipped:
    """Lock the delivery row and decode its fact, if it should run now."""
    async with conn.cursor(row_factory=class_row(_DeliveryRow)) as cur:
        await cur.execute(
            """
            SELECT d.seq, d.not_before, d.attempts, d.handled_at,
                   o.message_type, o.schema_version, o.payload::text
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
    fact = decode(
        delivery.message_type,
        delivery.schema_version,
        delivery.payload.encode("utf-8"),
    )
    if isinstance(fact, UndecodableFact):
        # We wrote it ourselves, so this is a bug.
        raise ValueError(f"undecodable fact for {candidate}: {fact}")
    return fact


async def _finish_delivery(
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


async def _defer(
    conn: Connection, candidate: Candidate, reason: str, now: datetime
) -> datetime:
    """Leave the delivery open and set when to try again. Time only
    triggers the retry; what happens then is decided under the locks."""
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


async def handle_delivery(
    conn: Connection,
    candidate: Candidate,
    consumer: LocalConsumer,
    environment: Environment,
    now: datetime,
) -> Ran | Skipped:
    """A local consumer's delivery, in the caller's transaction, under the
    aggregate's advisory lock. Never commits."""
    fact = await _open_delivery(conn, candidate, now)
    if isinstance(fact, Skipped):
        return fact
    outcome = await consumers.handle(conn, consumer, fact, environment, now)
    await _finish_delivery(conn, candidate, now)
    return Ran(outcome)


@dataclass(frozen=True)
class Prepared:
    """An effect consumer's `prepare` committed a call to make."""

    fact: Fact
    call: EffectCall


async def prepare_delivery(
    conn: Connection, candidate: Candidate, consumer: EffectConsumer, now: datetime
) -> Prepared | Ran | Skipped:
    """An effect consumer's first transaction. Never commits."""
    fact = await _open_delivery(conn, candidate, now)
    if isinstance(fact, Skipped):
        return fact
    prepared = await consumers.prepare(conn, consumer, fact, now)
    if isinstance(prepared, NoCall):
        await _finish_delivery(conn, candidate, now)
        return Ran(prepared.outcome)
    return Prepared(fact, prepared)


async def complete_delivery(
    conn: Connection,
    candidate: Candidate,
    fact: Fact,
    answer: EffectAnswer,
    now: datetime,
) -> Ran | Deferred | Again:
    """An effect consumer's last transaction. Never commits."""
    step = await consumers.complete(conn, fact, answer, now)
    match step:
        case Done(outcome=outcome):
            await _finish_delivery(conn, candidate, now)
            return Ran(outcome)
        case RetryLater(reason=reason):
            not_before = await _defer(conn, candidate, reason, now)
            return Deferred(reason, not_before)
        case Again():
            return step
        case _:
            assert_never(step)


async def run_effect(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    consumer: EffectConsumer,
    now: datetime,
) -> Ran | Skipped | Deferred:
    """prepare (commit), call with no transaction open, complete (commit),
    again while `complete` asks for it. A crash between any two leaves the
    committed state for the next run's `prepare` to find."""
    for _ in range(MAX_EFFECT_STEPS):
        async with conn.transaction():
            prepared = await prepare_delivery(conn, candidate, consumer, now)
        if not isinstance(prepared, Prepared):
            return prepared
        answer = await consumers.call(http, environment, prepared.call)
        async with conn.transaction():
            completed = await complete_delivery(
                conn, candidate, prepared.fact, answer, now
            )
        if not isinstance(completed, Again):
            return completed
    raise AssertionError(f"{candidate} did not finish in {MAX_EFFECT_STEPS} steps")


def backoff(attempts: int) -> timedelta:
    """Wait before retry number `attempts` (1, 2, …): 2, 4, 8 … seconds,
    capped at an hour."""
    return min(timedelta(seconds=2 ** min(attempts, 20)), MAX_BACKOFF)


async def run_one(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    now: datetime,
) -> RunResult:
    """Lock the aggregate, run one delivery, unlock. `conn` is the worker's
    dedicated autocommit connection."""
    if not await try_lock_aggregate(conn, candidate):
        return Skipped("busy")
    try:
        try:
            match consumer_kind(candidate.consumer):
                case Local(consumer=local):
                    async with conn.transaction():
                        return await handle_delivery(
                            conn, candidate, local, environment, now
                        )
                case Effect(consumer=effect):
                    return await run_effect(
                        conn, http, environment, candidate, effect, now
                    )
        except Exception as error:
            return await _record_failure(conn, candidate, repr(error), now)
    finally:
        await unlock_aggregate(conn, candidate)


async def _record_failure(
    conn: Connection, candidate: Candidate, error: str, now: datetime
) -> Failed:
    async with conn.transaction():
        not_before = await _defer(conn, candidate, error, now)
    return Failed(error, not_before)


async def drain_once(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    now: datetime,
    limit: int = 100,
) -> list[RunResult]:
    """Try each due candidate once. Returns what happened to each."""
    results: list[RunResult] = []
    for candidate in await find_candidates(conn, now, limit):
        results.append(await run_one(conn, http, environment, candidate, now))
    return results


async def next_retry_at(conn: Connection, now: datetime) -> datetime | None:
    """The earliest future `not_before` among deliveries that could run next
    (the oldest unhandled per consumer and aggregate). Ones already due are
    left out: they are busy on another worker or blocked behind an older
    one, and counting them would make an idle worker spin."""
    cur = await conn.execute(
        """
        SELECT min(not_before)
        FROM (
          SELECT DISTINCT ON (consumer, aggregate_type, aggregate_id) not_before
          FROM outbox_delivery
          WHERE handled_at IS NULL
          ORDER BY consumer, aggregate_type, aggregate_id, seq
        ) AS oldest
        WHERE not_before > %(now)s
        """,
        {"now": now},
    )
    row = await cur.fetchone()
    assert row is not None
    retry_at = row[0]
    assert retry_at is None or isinstance(retry_at, datetime)
    return retry_at


def idle_wait(
    now: datetime, retry_at: datetime | None, fallback: timedelta
) -> timedelta:
    """How long an idle worker may wait before looking again."""
    if retry_at is None:
        return fallback
    return max(timedelta(0), min(retry_at - now, fallback))


async def listen(conn: Connection) -> None:
    """Subscribe the worker's autocommit connection to new deliveries.
    psycopg queues notifications that arrive while the worker is busy, so
    one sent during a drain still wakes the next wait at once."""
    await conn.execute("LISTEN outbox_delivery")


async def wait_for_work(conn: Connection, wait: timedelta) -> bool:
    """Wait for a notification, at most `wait`. True if one came."""
    async for _ in conn.notifies(timeout=wait.total_seconds(), stop_after=1):
        return True
    return False


async def run_worker(settings: Settings) -> None:
    """Drain until nothing runs, then wait for a notification, the next
    retry or the fallback poll. Runs until cancelled or killed: a step in
    flight rolls back, and its advisory lock goes with the connection."""
    async with (
        await connect(settings.database_url, autocommit=True) as conn,
        httpx2.AsyncClient() as http,
    ):
        await listen(conn)
        while True:
            now = datetime.now(UTC)
            results = await drain_once(conn, http, settings.environment, now)
            ran = False
            for result in results:
                if isinstance(result, Failed):
                    retry_at = format_moment(result.not_before)
                    print(f"failed: {result.error}; retry at {retry_at}")
                if isinstance(result, Ran):
                    ran = True
            if not ran:
                retry_at = await next_retry_at(conn, now)
                await wait_for_work(conn, idle_wait(now, retry_at, FALLBACK_POLL))


def _run(settings: Settings) -> None:
    """One worker process. Started by watchfiles outside prod, which stops
    it with SIGINT on a change: a crash the worker is built to survive."""
    # A shell's background job starts with SIGINT ignored, and children
    # keep that: the worker would then only stop when killed (seen
    # 19-Nov-2026). Python's own handler makes SIGINT stop it again.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        asyncio.run(run_worker(settings))
    except KeyboardInterrupt:  # Ctrl-C, or a reload
        pass


def _reloading(changes: set[tuple[watchfiles.Change, str]]) -> None:
    paths: list[str] = []
    for _change, path in changes:
        paths.append(Path(path).relative_to(SOURCE).as_posix())
    print(f"worker: {', '.join(sorted(paths))} changed, restarting", flush=True)


if __name__ == "__main__":
    settings = load_settings(os.environ)
    if settings.environment == Environment.PROD:
        _run(settings)
    else:
        # Restarted on a change under src/; a reload does not migrate,
        # load markets or seed.
        watchfiles.run_process(
            SOURCE,
            target=_run,
            args=(settings,),
            watch_filter=PythonFilter(),
            callback=_reloading,
        )
