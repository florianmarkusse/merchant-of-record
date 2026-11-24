"""Polling an echo job's events, for `poll` accounts and thin webhooks
(docs/plan-messaging.md §12). Transitional with the echo flow.

    echo_job_echoed ─▶ echo_tracking_start: on a `poll` account, schedule
                       the first echo_poll_due
    echo_poll_due  ┐
    echo_events_wanted (thin webhook) ┴▶ echo_poll (effect): fetch one page
                       of events, store it raw in the inbox (the same path as
                       a webhook), and on a scheduled poll schedule the next,
                       until the job's status is final

A fetch only reads at the vendor, so a fetch repeated after a crash needs no
request row and no guard: it stores the same bytes, which the inbox key
absorbs.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import registry
from mor.echo.events import VendorStatus
from mor.echo.inbound import inbound_account, store_inbox
from mor.echo.port import (
    EchoAccount,
    EventMode,
    FetchResult,
    Page,
)
from mor.messaging.facts import (
    EchoEventsWantedV1,
    EchoPollDueV1,
)
from mor.messaging.outcome import (
    Applied,
    ConsumerOutcome,
    Done,
    NoCall,
    RetryLater,
    Stale,
)
from mor.messaging.store import append_fact
from mor.vendor_failures import OutcomeUnknown, Unavailable


SCHEDULER = Actor(ActorKind.SYSTEM, "echo_poll")

# The last status a job can reach: nothing is polled after it.
FINAL = VendorStatus.ARCHIVED


@dataclass(frozen=True)
class FetchEventsCall:
    account: EchoAccount
    account_id: uuid.UUID
    reference: str


@dataclass(frozen=True)
class FetchEventsAnswer:
    """The fetch and what the vendor said to it, from `call` to `complete`."""

    call: FetchEventsCall
    result: FetchResult


@dataclass(frozen=True)
class TrackedJob:
    """An echo job's tracking columns, read under its row lock."""

    id: uuid.UUID
    account_id: uuid.UUID
    vendor_status: VendorStatus


async def lock_tracked_job(conn: Connection, job_id: uuid.UUID) -> TrackedJob:
    async with conn.cursor(row_factory=class_row(TrackedJob)) as cur:
        await cur.execute(
            "SELECT id, account_id, vendor_status FROM echo_job"
            " WHERE id = %s FOR UPDATE",
            (job_id,),
        )
        job = await cur.fetchone()
    if job is None:
        raise LookupError(f"echo job {job_id} does not exist")
    return job


async def start_tracking(
    conn: Connection, job: TrackedJob, now: datetime
) -> ConsumerOutcome:
    """Consumer `echo_tracking_start`, on echo_job_echoed, the job locked: a
    `poll` account gets its first poll now; other modes wait for the
    vendor or a person."""
    job_id = job.id
    inbound = await inbound_account(conn, job.account_id)
    assert inbound is not None
    if inbound.event_mode != EventMode.POLL:
        return Stale()
    await append_fact(conn, EchoPollDueV1(echo_job_id=job_id), SCHEDULER, now)
    return Applied()


# The consumer echo_poll gets either fact; only a poll schedules the next.
type PollWanted = EchoPollDueV1 | EchoEventsWantedV1


async def prepare_poll(
    conn: Connection, wanted: PollWanted, now: datetime
) -> NoCall | FetchEventsCall:
    """Under the job's lock: is a fetch still wanted? Not once the status is
    final, whatever started it."""
    job_id = wanted.echo_job_id
    job = await lock_tracked_job(conn, job_id)
    if job.vendor_status == FINAL:
        return NoCall(Stale())
    inbound = await inbound_account(conn, job.account_id)
    assert inbound is not None
    return FetchEventsCall(inbound.account, job.account_id, str(job_id))


async def call_poll(
    http: httpx2.AsyncClient, environment: Environment, fetch: FetchEventsCall
) -> FetchEventsAnswer:
    result = await registry.fetch_events(
        http, environment, fetch.account, fetch.reference
    )
    return FetchEventsAnswer(fetch, result)


async def complete_poll(
    conn: Connection,
    wanted: PollWanted,
    fetch: FetchEventsCall,
    answer: FetchEventsAnswer,
    now: datetime,
) -> Done | RetryLater:
    job_id = wanted.echo_job_id
    job = await lock_tracked_job(conn, job_id)
    match answer.result:
        case Page(body=body):
            await store_inbox(conn, answer.call.account_id, EventMode.POLL, body, now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # A read: trying again later is always safe.
            return RetryLater(f"poll: {detail}")
    match wanted:
        case EchoPollDueV1():
            # The page just stored is not applied yet, so a final status in
            # it is seen by the next poll, which then stops.
            if job.vendor_status != FINAL:
                inbound = await inbound_account(conn, job.account_id)
                assert inbound is not None
                await append_fact(
                    conn,
                    EchoPollDueV1(echo_job_id=job_id),
                    SCHEDULER,
                    now,
                    deliver_at=now + inbound.poll_interval,
                )
        case EchoEventsWantedV1():
            pass  # a thin webhook's fetch schedules nothing
    return Done(Applied())
