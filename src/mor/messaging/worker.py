"""The worker: runs due deliveries, in no particular order.

    uv run python -m mor.messaging.worker      # DATABASE_URL, default mor_dev

docs/plan-messaging.md §4: pick due deliveries, lock the delivery row
(skip it if another worker has it), run the consumer, mark it handled.
An effect consumer also holds the session advisory lock on the fact's
aggregate from `prepare` to `complete`, across its vendor call.
Consumers take facts in any order. A lock guards; time only decides when
to try again (`not_before`)."""

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import watchfiles
from psycopg.rows import class_row
from watchfiles import PythonFilter

from mor.config import Environment, Settings, load_settings
from mor.dates import format_moment
from mor.db.connect import Connection, connect
from mor.db.fetch import one_value
from mor.messaging import consumers
from mor.messaging.delivery import (
    Candidate,
    Deferred,
    Ran,
    Skipped,
    defer_delivery,
    finish_delivery,
    open_delivery,
)
from mor.messaging.routing import Effect, Local, LocalConsumer, consumer_kind


# An idle worker waits for a NOTIFY on channel `outbox_delivery`
# (0003_delivery_notify.sql), for the next retry to come due, or at most this
# long: notifications sent while no worker listens are not kept, so polling
# stays as the fallback.
FALLBACK_POLL = timedelta(seconds=5)
SOURCE = Path(__file__).resolve().parent.parent.parent  # src/, watched outside prod


@dataclass(frozen=True)
class Failed:
    """The consumer raised: a bug. Rolled back and retried after a backoff;
    other deliveries do not wait for it."""

    error: str
    not_before: datetime


type RunResult = Ran | Skipped | Deferred | Failed


async def find_candidates(
    conn: Connection, now: datetime, limit: int
) -> list[Candidate]:
    """Unhandled deliveries that are due, oldest first. Reading only: the
    delivery's row lock decides who runs it."""
    async with conn.cursor(row_factory=class_row(Candidate)) as cur:
        await cur.execute(
            """
            SELECT message_id, consumer, aggregate_type, aggregate_id
            FROM outbox_delivery
            WHERE handled_at IS NULL AND not_before <= %(now)s
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
    return await one_value(
        conn,
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        (_lock_name(candidate),),
        bool,
    )


async def unlock_aggregate(conn: Connection, candidate: Candidate) -> None:
    unlocked = await one_value(
        conn,
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
        (_lock_name(candidate),),
        bool,
    )
    if not unlocked:
        raise AssertionError(f"unlocked a lock not held: {candidate}")


async def handle_delivery(
    conn: Connection,
    candidate: Candidate,
    consumer: LocalConsumer,
    environment: Environment,
    now: datetime,
) -> Ran | Skipped:
    """A local consumer's delivery, in the caller's transaction, under the
    delivery's row lock. Never commits."""
    fact = await open_delivery(conn, candidate, now)
    if isinstance(fact, Skipped):
        return fact
    outcome = await consumers.run_local(conn, consumer, fact, environment, now)
    await finish_delivery(conn, candidate, now)
    return Ran(outcome)


async def run_one(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    now: datetime,
) -> RunResult:
    """Run one delivery. A local consumer runs in one transaction under the
    delivery's row lock and locks the rows it changes. An effect consumer
    spans commits, so it also holds the aggregate's advisory lock: without
    it a second worker's `prepare` would take a call still in flight for a
    dead one. `conn` is the worker's dedicated autocommit connection."""
    match consumer_kind(candidate.consumer):
        case Local(consumer=local):
            try:
                async with conn.transaction():
                    return await handle_delivery(
                        conn, candidate, local, environment, now
                    )
            except Exception as error:
                return await _record_failure(conn, candidate, repr(error), now)
        case Effect(consumer=effect):
            if not await try_lock_aggregate(conn, candidate):
                return Skipped("busy")
            try:
                # Its own short transaction: the advisory lock keeps every
                # other worker off this delivery until the consumer ends it.
                async with conn.transaction():
                    fact = await open_delivery(conn, candidate, now)
                if isinstance(fact, Skipped):
                    return fact
                return await consumers.run_effect(
                    conn, http, environment, candidate, effect, fact, now
                )
            except Exception as error:
                return await _record_failure(conn, candidate, repr(error), now)
            finally:
                await unlock_aggregate(conn, candidate)


async def _record_failure(
    conn: Connection, candidate: Candidate, error: str, now: datetime
) -> Failed:
    async with conn.transaction():
        not_before = await defer_delivery(conn, candidate, error, now)
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


@dataclass(frozen=True)
class _RetryRow:
    not_before: datetime


async def next_retry_at(conn: Connection, now: datetime) -> datetime | None:
    """The earliest future `not_before` among unhandled deliveries. Ones
    already due are left out: they are busy on another worker, and counting
    them would make an idle worker spin."""
    async with conn.cursor(row_factory=class_row(_RetryRow)) as cur:
        await cur.execute(
            """
            SELECT not_before FROM outbox_delivery
            WHERE handled_at IS NULL AND not_before > %(now)s
            ORDER BY not_before LIMIT 1
            """,
            {"now": now},
        )
        retry = await cur.fetchone()
    return None if retry is None else retry.not_before


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
