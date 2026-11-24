"""Inbound events for the echo flow (docs/plan-messaging.md §12), one path
for every mode:

    raw bytes ─▶ store_inbox (unique per account + sha256) + echo_inbox_stored
              ─▶ decode_inbox (consumer, per account): one
                 echo_vendor_event_received per event
    by hand   ─▶ record_manual_event: the same fact, source `manual`
              ─▶ apply_event (consumer, per job): the event once, the status
                 from the set of events, each milestone once

Transitional with the echo flow; it rehearses shipment tracking.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.echo import registry
from mor.echo.events import VendorStatus, derive_status, rank
from mor.echo.port import (
    EchoAccount,
    EchoProvider,
    EventKind,
    EventMode,
)
from mor.echo.records import EchoEventSource
from mor.messaging.facts import (
    EchoEventsWantedV1,
    EchoInboxStoredV1,
    EchoJobArchivedV1,
    EchoJobConfirmedV1,
    EchoVendorEventReceivedV1,
    Fact,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.store import append_fact
from mor.provider_accounts import ProviderAccountKind
from mor.vendor_failures import UndecodableBody


DECODER = Actor(ActorKind.SYSTEM, "echo_inbox_decode")
APPLIER = Actor(ActorKind.SYSTEM, "echo_event_apply")
RECEIVER = Actor(ActorKind.SYSTEM, "echo_inbox")


class UndecodableInbox(Exception):
    """Stored bytes the decoder cannot read. The raw row stays; the delivery
    fails visibly and is retried, so a fixed decoder handles it later."""


class UnknownReference(Exception):
    """An event for a job this account never sent. Only a vendor bug can
    cause it here; a real port turns it into an exception row for a person
    (plan-architecture.md §9 does this for courier invoice lines)."""


@dataclass(frozen=True, kw_only=True)
class InboundAccount:
    account: EchoAccount
    event_mode: EventMode
    webhook_secret: str
    poll_interval: timedelta


@dataclass(frozen=True)
class Stored:
    inbox_id: uuid.UUID


@dataclass(frozen=True)
class AlreadyStored:
    """The same bytes arrived before (a webhook resent, a page fetched
    again); nothing new was written."""


@dataclass(frozen=True)
class JobNotFound:
    job_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: uuid.UUID
    provider: EchoProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int
    event_mode: EventMode
    webhook_secret: str
    poll_interval_s: int


async def inbound_account(
    conn: Connection, account_id: uuid.UUID
) -> InboundAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT id, provider, kind, base_url, timeout_ms, event_mode,
                   webhook_secret, poll_interval_s
            FROM echo_account WHERE id = %s
            """,
            (account_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    account = EchoAccount(
        id=row.id,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
    )
    return InboundAccount(
        account=account,
        event_mode=row.event_mode,
        webhook_secret=row.webhook_secret,
        poll_interval=timedelta(seconds=row.poll_interval_s),
    )


async def store_inbox(
    conn: Connection,
    account_id: uuid.UUID,
    mode: EventMode,
    body: bytes,
    now: datetime,
) -> Stored | AlreadyStored:
    """Keep the raw bytes, nothing decoded, with the fact that starts the
    decoding, in the caller's transaction. The unique key decides
    "already stored": insert, and a conflict means it was. Never commits."""
    inbox_id = uuid.uuid7()
    cur = await conn.execute(
        """
        INSERT INTO echo_inbox (id, account_id, mode, body_sha256, body, received_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, body_sha256) DO NOTHING
        RETURNING id
        """,
        (
            inbox_id,
            account_id,
            mode.value,
            hashlib.sha256(body).hexdigest(),
            body,
            now,
        ),
    )
    if await cur.fetchone() is None:
        return AlreadyStored()
    await append_fact(
        conn, EchoInboxStoredV1(account_id=account_id, inbox_id=inbox_id), RECEIVER, now
    )
    return Stored(inbox_id)


@dataclass(frozen=True)
class _InboxRow:
    mode: EventMode
    body: bytes


async def decode_inbox(
    conn: Connection, stored_fact: EchoInboxStoredV1, now: datetime
) -> ConsumerOutcome:
    """Consumer `echo_inbox_decode`, per account: the stored bytes into one
    fact per event, each for its own job (one step locks one aggregate).
    Locks nothing: an inbox row never changes."""
    account_id = stored_fact.account_id
    inbox_id = stored_fact.inbox_id
    async with conn.cursor(row_factory=class_row(_InboxRow)) as cur:
        await cur.execute(
            "SELECT mode, body FROM echo_inbox WHERE id = %s", (inbox_id,)
        )
        stored = await cur.fetchone()
    assert stored is not None, f"no inbox row {inbox_id}"
    mode = stored.mode
    body = stored.body
    inbound = await inbound_account(conn, account_id)
    assert inbound is not None, f"no echo account {account_id}"

    if mode == EventMode.PUSH_THIN:
        # Only "this job has news": fetching what the news is, is a poll.
        reference = registry.decode_thin_webhook(inbound.account, body)
        if isinstance(reference, UndecodableBody):
            raise UndecodableInbox(f"inbox {inbox_id}: {reference.detail}")
        job_id = await _job_of_account(conn, account_id, reference)
        if job_id is None:
            raise UnknownReference(f"inbox {inbox_id}: no job {reference!r}")
        await append_fact(conn, EchoEventsWantedV1(echo_job_id=job_id), DECODER, now)
        return Applied()

    events = registry.decode_events(inbound.account, mode, body)
    if isinstance(events, UndecodableBody):
        raise UndecodableInbox(f"inbox {inbox_id}: {events.detail}")
    for event in events:
        job_id = await _job_of_account(conn, account_id, event.reference)
        if job_id is None:
            raise UnknownReference(f"inbox {inbox_id}: no job {event.reference!r}")
        await append_fact(
            conn,
            EchoVendorEventReceivedV1(
                echo_job_id=job_id,
                vendor_event_id=event.vendor_event_id,
                kind=event.kind,
                occurred_at=event.occurred_at,
                source=EchoEventSource.VENDOR,
                inbox_id=inbox_id,
            ),
            DECODER,
            now,
        )
    return Applied()


async def _job_of_account(
    conn: Connection, account_id: uuid.UUID, reference: str
) -> uuid.UUID | None:
    try:
        job_id = uuid.UUID(reference)
    except ValueError:
        return None
    cur = await conn.execute(
        "SELECT id FROM echo_job WHERE id = %s AND account_id = %s",
        (job_id, account_id),
    )
    row = await cur.fetchone()
    return None if row is None else job_id


async def record_manual_event(
    conn: Connection,
    job_id: uuid.UUID,
    kind: EventKind,
    occurred_at: datetime,
    actor: Actor,
    now: datetime,
) -> uuid.UUID | JobNotFound:
    """A person records what they saw (on the vendor's website, say). It
    enters where a decoded vendor event does, with the person as actor.
    Never commits."""
    cur = await conn.execute("SELECT 1 FROM echo_job WHERE id = %s", (job_id,))
    if await cur.fetchone() is None:
        return JobNotFound(job_id)
    return await append_fact(
        conn,
        EchoVendorEventReceivedV1(
            echo_job_id=job_id,
            vendor_event_id=f"manual:{uuid.uuid7()}",
            kind=kind,
            occurred_at=occurred_at,
            source=EchoEventSource.MANUAL,
            inbox_id=None,
        ),
        actor,
        now,
    )


async def lock_job_status(conn: Connection, job_id: uuid.UUID) -> VendorStatus:
    """An echo job's vendor status, under the job's row lock."""
    return await one_value(
        conn,
        "SELECT vendor_status FROM echo_job WHERE id = %s FOR UPDATE",
        (job_id,),
        VendorStatus,
    )


async def apply_event(
    conn: Connection,
    current: VendorStatus,
    event: EchoVendorEventReceivedV1,
    now: datetime,
) -> ConsumerOutcome:
    """Consumer `echo_event_apply`, per job, the job locked (`current` is
    its status then): record the event once, derive the status from every
    event the job has, and emit each milestone the first time it is
    reached."""

    cur = await conn.execute(
        """
        INSERT INTO echo_job_event
          (echo_job_id, vendor_event_id, kind, occurred_at, source, inbox_id,
           recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (echo_job_id, vendor_event_id) DO NOTHING
        RETURNING vendor_event_id
        """,
        (
            event.echo_job_id,
            event.vendor_event_id,
            event.kind,
            event.occurred_at,
            event.source,
            event.inbox_id,
            now,
        ),
    )
    if await cur.fetchone() is None:
        return Stale()  # the same event, already recorded

    cur = await conn.execute(
        "SELECT kind FROM echo_job_event WHERE echo_job_id = %s",
        (event.echo_job_id,),
    )
    kinds: list[EventKind] = []
    for (kind,) in await cur.fetchall():
        kinds.append(kind)
    status = derive_status(kinds)
    if rank(status) <= rank(current):
        return Applied()

    await conn.execute(
        "UPDATE echo_job SET vendor_status = %s, updated_at = %s WHERE id = %s",
        (status.value, now, event.echo_job_id),
    )
    cur = await conn.execute(
        """
        INSERT INTO echo_job_milestone (echo_job_id, status, reached_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (echo_job_id, status) DO NOTHING
        RETURNING status
        """,
        (event.echo_job_id, status.value, now),
    )
    if await cur.fetchone() is not None:
        milestone: Fact
        match status:
            case VendorStatus.CONFIRMED:
                milestone = EchoJobConfirmedV1(echo_job_id=event.echo_job_id)
            case VendorStatus.ARCHIVED:
                milestone = EchoJobArchivedV1(echo_job_id=event.echo_job_id)
            case VendorStatus.NONE:
                raise AssertionError("no milestone for `none`")
        await append_fact(conn, milestone, APPLIER, now)
    return Applied()
