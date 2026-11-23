"""What the PSP sends us, one path for every mode (docs/plan-payments.md
§4; docs/plan-messaging.md §12):

    raw bytes (webhook or fetched page) ─▶ store_inbox (unique per account
        and body hash) + psp_inbox_stored
    psp_inbox_decode (per account): a thin notice becomes
        payment_events_wanted; events become one psp_event_received each;
        a settlement report or page becomes its batch and lines
        (mor.settlement.store, docs/plan-payments.md §7.1)
    payment_event_apply (per attempt): the event once, by the PSP's id,
        through the transition table

The guard against a duplicate event is the PSP's event id; the body hash
only means "these bytes are stored already" (decided 17-Nov-2026).
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.messaging.facts import (
    Fact,
    PaymentEventsWantedV1,
    PspEventReceivedV1,
    PspInboxStoredV1,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.attempts import attempt_of_account, lock_attempt, move
from mor.payments.transitions import Stale as StaleEvent
from mor.payments.transitions import after_event
from mor.psp import registry
from mor.psp.accounts import read_psp_account
from mor.psp.port import (
    InboundMode,
)
from mor.settlement.store import record_document
from mor.vendor_failures import UndecodableBody


RECEIVER = Actor(ActorKind.SYSTEM, "psp_inbox")
DECODER = Actor(ActorKind.SYSTEM, "psp_inbox_decode")
APPLIER = Actor(ActorKind.SYSTEM, "payment_event_apply")


class UndecodableInbox(Exception):
    """Stored bytes the decoder cannot read. The raw row stays; the delivery
    fails visibly and is retried, so a fixed decoder handles it later."""


class UnknownReference(Exception):
    """An event for a payment this account never made. Only a PSP bug can
    cause it; a person looks at the stored bytes."""


@dataclass(frozen=True)
class Stored:
    inbox_id: uuid.UUID


@dataclass(frozen=True)
class AlreadyStored:
    """The same bytes arrived before (a webhook resent, a page fetched
    again): nothing new was written."""


async def store_inbox(
    conn: Connection,
    account_id: uuid.UUID,
    mode: InboundMode,
    body: bytes,
    request_ref: str | None,
    now: datetime,
) -> Stored | AlreadyStored:
    """Keep the raw bytes, nothing decoded, with the fact that starts the
    decoding. `request_ref` is what we asked for, when the bytes do not say
    (the payout a page of lines belongs to); None for what the PSP sends
    on its own. Insert, and a conflict means stored already. Never
    commits."""
    inbox_id = uuid.uuid7()
    cur = await conn.execute(
        """
        INSERT INTO psp_inbox
          (id, account_id, mode, body_sha256, body, request_ref, received_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, body_sha256) DO NOTHING
        RETURNING id
        """,
        (
            inbox_id,
            account_id,
            mode.value,
            hashlib.sha256(body).hexdigest(),
            body,
            request_ref,
            now,
        ),
    )
    if await cur.fetchone() is None:
        return AlreadyStored()
    await append_fact(
        conn,
        PspInboxStoredV1(psp_account_id=account_id, inbox_id=inbox_id),
        RECEIVER,
        now,
    )
    return Stored(inbox_id)


@dataclass(frozen=True)
class _InboxRow:
    mode: InboundMode
    body: bytes
    request_ref: str | None


async def decode_inbox(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `psp_inbox_decode`, per account: the stored bytes into one
    fact per event, each for its own attempt (one step locks one
    aggregate)."""
    match fact:
        case PspInboxStoredV1(psp_account_id=account_id, inbox_id=inbox_id):
            pass
        case _:
            raise WrongFact(f"psp_inbox_decode got {fact!r}")
    async with conn.cursor(row_factory=class_row(_InboxRow)) as cur:
        await cur.execute(
            "SELECT mode, body, request_ref FROM psp_inbox WHERE id = %s", (inbox_id,)
        )
        stored = await cur.fetchone()
    assert stored is not None, f"no inbox row {inbox_id}"
    mode = stored.mode
    body = stored.body
    account = await read_psp_account(conn, account_id)
    assert account is not None, f"no psp account {account_id}"

    match mode:
        case (
            InboundMode.SETTLEMENT_FILE
            | InboundMode.SETTLEMENT_PAYOUT
            | InboundMode.SETTLEMENT_PAGE
        ):
            # A report, a payout or a page of its lines
            # (docs/plan-settlement-reconciliation.md §4.5).
            failed = await record_document(
                conn, account, inbox_id, mode, body, stored.request_ref, now
            )
            if failed is not None:
                raise UndecodableInbox(f"inbox {inbox_id}: {failed.detail}")
            return Applied()
        case InboundMode.PUSH_FAT | InboundMode.PUSH_THIN | InboundMode.POLL:
            pass

    if mode == InboundMode.PUSH_THIN:
        notice = registry.decode_thin_webhook(account, body)
        if isinstance(notice, UndecodableBody):
            raise UndecodableInbox(f"inbox {inbox_id}: {notice.detail}")
        attempt_id = await attempt_of_account(conn, account_id, notice.reference)
        if attempt_id is None:
            raise UnknownReference(f"inbox {inbox_id}: {notice.reference!r}")
        await append_fact(
            conn, PaymentEventsWantedV1(payment_attempt_id=attempt_id), DECODER, now
        )
        return Applied()

    events = registry.decode_events(account, body)
    if isinstance(events, UndecodableBody):
        raise UndecodableInbox(f"inbox {inbox_id}: {events.detail}")
    for event in events:
        attempt_id = await attempt_of_account(conn, account_id, event.reference)
        if attempt_id is None:
            raise UnknownReference(f"inbox {inbox_id}: {event.reference!r}")
        await append_fact(
            conn,
            PspEventReceivedV1(
                payment_attempt_id=attempt_id,
                vendor_event_id=event.vendor_event_id,
                provider_payment_id=event.provider_payment_id,
                kind=event.kind,
                amount_minor=event.amount.amount_minor,
                currency=event.amount.currency.value,
                occurred_at=event.occurred_at,
                inbox_id=inbox_id,
            ),
            DECODER,
            now,
        )
    return Applied()


async def apply_event(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `payment_event_apply`, per attempt: record the event once,
    and move the attempt as the transition table says."""
    match fact:
        case PspEventReceivedV1() as event:
            pass
        case _:
            raise WrongFact(f"payment_event_apply got {fact!r}")
    attempt = await lock_attempt(conn, event.payment_attempt_id)
    cur = await conn.execute(
        """
        INSERT INTO payment_event
          (attempt_id, vendor_event_id, kind, amount_minor, currency, occurred_at,
           inbox_id, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (attempt_id, vendor_event_id) DO NOTHING
        RETURNING vendor_event_id
        """,
        (
            attempt.id,
            event.vendor_event_id,
            event.kind,
            event.amount_minor,
            event.currency,
            event.occurred_at,
            event.inbox_id,
            now,
        ),
    )
    if await cur.fetchone() is None:
        return Stale()  # the same event, already recorded
    if attempt.provider_payment_id is None:
        # The webhook beat the create call's answer: keep the PSP's id now.
        await conn.execute(
            "UPDATE payment_attempt SET provider_payment_id = %s WHERE id = %s",
            (event.provider_payment_id, attempt.id),
        )
    target = after_event(attempt.state, event.kind)
    if isinstance(target, StaleEvent):
        return Applied()
    account = await read_psp_account(conn, attempt.psp_account_id)
    assert account is not None
    await move(
        conn,
        attempt,
        target,
        account,
        now,
        None,
        Money(event.amount_minor, Currency(event.currency)),
        APPLIER,
    )
    return Applied()
