"""Payment attempts in the database, and `move`: the one place an attempt
changes state, writing the fact that says so (docs/plan-payments.md §4).
Every function runs under the caller's transaction and never commits;
`move` expects the attempt's row lock to be held."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.messaging.facts import (
    Fact,
    PaymentActionRequiredV1,
    PaymentAttemptCreatedV1,
    PaymentAuthorisedV1,
    PaymentCancelledV1,
    PaymentCancelWantedV1,
    PaymentCapturedV1,
    PaymentDeclinedV1,
    PaymentExpiredV1,
    PaymentNeedsPersonV1,
    PaymentPollDueV1,
)
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.payments.transitions import AttemptState
from mor.psp.port import BuyerAction, PaymentMethod, PspAccount, StatusMode


FLOW = Actor(ActorKind.SYSTEM, "payments")


@dataclass(frozen=True)
class Attempt:
    id: uuid.UUID
    session_id: uuid.UUID
    quote_id: uuid.UUID
    option_no: int
    psp_account_id: uuid.UUID
    route_id: uuid.UUID
    method: PaymentMethod
    amount: Money
    return_url: str
    state: AttemptState
    provider_payment_id: str | None
    action: BuyerAction | None
    ended_reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    session_id: uuid.UUID
    quote_id: uuid.UUID
    option_no: int
    psp_account_id: uuid.UUID
    route_id: uuid.UUID
    method: str
    amount_minor: int
    currency: str
    return_url: str
    state: str
    provider_payment_id: str | None
    action_kind: str | None
    action_url: str | None
    action_expires_at: datetime | None
    ended_reason: str | None
    created_at: datetime


def _attempt(row: _Row) -> Attempt:
    action: BuyerAction | None = None
    if row.action_url is not None:
        assert row.action_kind == "redirect" or row.action_kind == "qr"
        action = BuyerAction(row.action_kind, row.action_url, row.action_expires_at)
    return Attempt(
        row.id,
        row.session_id,
        row.quote_id,
        row.option_no,
        row.psp_account_id,
        row.route_id,
        PaymentMethod(row.method),
        Money(row.amount_minor, Currency(row.currency)),
        row.return_url,
        AttemptState(row.state),
        row.provider_payment_id,
        action,
        row.ended_reason,
        row.created_at,
    )


async def insert_attempt(
    conn: Connection,
    session_id: uuid.UUID,
    quote_id: uuid.UUID,
    option_no: int,
    account: PspAccount,
    route_id: uuid.UUID,
    method: PaymentMethod,
    amount: Money,
    return_url: str,
    actor: Actor,
    now: datetime,
) -> uuid.UUID:
    """A new attempt, `requested`, with the fact that starts the call. The
    one-open index refuses a second one for the session; the caller holds
    the session's lock and has checked."""
    attempt_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO payment_attempt
          (id, session_id, quote_id, option_no, psp_account_id, route_id,
           method, amount_minor, currency, return_url, state, created_at,
           updated_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'requested', %s, %s, %s,
                %s)
        """,
        (
            attempt_id,
            session_id,
            quote_id,
            option_no,
            account.id,
            route_id,
            method.value,
            amount.amount_minor,
            amount.currency.value,
            return_url,
            now,
            now,
            actor.kind.value,
            actor.id,
        ),
    )
    await append_fact(
        conn,
        PaymentAttemptCreatedV1(
            payment_attempt_id=attempt_id, checkout_session_id=session_id
        ),
        actor,
        now,
    )
    return attempt_id


async def lock_attempt(conn: Connection, attempt_id: uuid.UUID) -> Attempt:
    async with conn.cursor(row_factory=class_row(_Row)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, quote_id, option_no, psp_account_id, route_id,
                   method::text, amount_minor, currency::text, return_url,
                   state::text,
                   provider_payment_id, action_kind::text, action_url,
                   action_expires_at, ended_reason, created_at
            FROM payment_attempt WHERE id = %s FOR UPDATE
            """,
            (attempt_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise LookupError(f"payment attempt {attempt_id} does not exist")
    return _attempt(row)


async def read_attempt(conn: Connection, attempt_id: uuid.UUID) -> Attempt | None:
    """Without a lock: for a step that reads another aggregate's attempt."""
    async with conn.cursor(row_factory=class_row(_Row)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, quote_id, option_no, psp_account_id, route_id,
                   method::text, amount_minor, currency::text, return_url,
                   state::text, provider_payment_id, action_kind::text, action_url,
                   action_expires_at, ended_reason, created_at
            FROM payment_attempt WHERE id = %s
            """,
            (attempt_id,),
        )
        row = await cur.fetchone()
    return None if row is None else _attempt(row)


async def session_attempts(conn: Connection, session_id: uuid.UUID) -> list[Attempt]:
    """Oldest first; the last is the newest."""
    async with conn.cursor(row_factory=class_row(_Row)) as cur:
        await cur.execute(
            """
            SELECT id, session_id, quote_id, option_no, psp_account_id, route_id,
                   method::text, amount_minor, currency::text, return_url,
                   state::text,
                   provider_payment_id, action_kind::text, action_url,
                   action_expires_at, ended_reason, created_at
            FROM payment_attempt WHERE session_id = %s ORDER BY id
            """,
            (session_id,),
        )
        rows = await cur.fetchall()
    found: list[Attempt] = []
    for row in rows:
        found.append(_attempt(row))
    return found


async def attempt_of_account(
    conn: Connection, account_id: uuid.UUID, reference: str
) -> uuid.UUID | None:
    """Our attempt a PSP's reference names, if it is this account's."""
    try:
        attempt_id = uuid.UUID(reference)
    except ValueError:
        return None
    cur = await conn.execute(
        "SELECT id FROM payment_attempt WHERE id = %s AND psp_account_id = %s",
        (attempt_id, account_id),
    )
    return None if await cur.fetchone() is None else attempt_id


async def record_payment(
    conn: Connection,
    attempt: Attempt,
    provider_payment_id: str,
    action: BuyerAction | None,
    now: datetime,
) -> Attempt:
    """The PSP's id for the payment and what the buyer must do, kept as the
    PSP gave them. The id never changes once known."""
    if attempt.provider_payment_id not in (None, provider_payment_id):
        raise AssertionError(
            f"attempt {attempt.id} is {attempt.provider_payment_id} at the PSP,"
            f" not {provider_payment_id}"
        )
    keep = attempt.action if action is None else action
    await conn.execute(
        """
        UPDATE payment_attempt
        SET provider_payment_id = %s, action_kind = %s, action_url = %s,
            action_expires_at = %s, updated_at = %s
        WHERE id = %s
        """,
        (
            provider_payment_id,
            None if keep is None else keep.kind,
            None if keep is None else keep.url,
            None if keep is None else keep.expires_at,
            now,
            attempt.id,
        ),
    )
    return Attempt(
        attempt.id,
        attempt.session_id,
        attempt.quote_id,
        attempt.option_no,
        attempt.psp_account_id,
        attempt.route_id,
        attempt.method,
        attempt.amount,
        attempt.return_url,
        attempt.state,
        provider_payment_id,
        keep,
        attempt.ended_reason,
        attempt.created_at,
    )


async def _other_capture(conn: Connection, attempt: Attempt) -> bool:
    cur = await conn.execute(
        """
        SELECT 1 FROM payment_attempt
        WHERE session_id = %s AND state = 'captured' AND id <> %s
        """,
        (attempt.session_id, attempt.id),
    )
    return await cur.fetchone() is not None


async def move(
    conn: Connection,
    attempt: Attempt,
    state: AttemptState,
    account: PspAccount,
    now: datetime,
    reason: str | None = None,
    captured: Money | None = None,
    actor: Actor = FLOW,
) -> AttemptState:
    """Set the attempt's state and write its fact. A capture of another
    amount than the attempt's, or a second capture in the session, is never
    booked: it goes to a person. Returns the state set."""
    if state == AttemptState.CAPTURED:
        if captured is not None and captured != attempt.amount:
            state = AttemptState.NEEDS_PERSON
            reason = f"captured {captured}, expected {attempt.amount}"
        elif await _other_capture(conn, attempt):
            state = AttemptState.NEEDS_PERSON
            reason = "a second capture for one session: refund it"
    await conn.execute(
        """
        UPDATE payment_attempt SET state = %s, ended_reason = %s, updated_at = %s
        WHERE id = %s
        """,
        (state.value, reason, now, attempt.id),
    )
    attempt_id = attempt.id
    session_id = attempt.session_id
    fact: Fact
    match state:
        case AttemptState.ACTION_REQUIRED:
            fact = PaymentActionRequiredV1(payment_attempt_id=attempt_id)
            await _schedule_polls(conn, attempt, account, now)
        case AttemptState.AUTHORISED:
            fact = PaymentAuthorisedV1(payment_attempt_id=attempt_id)
        case AttemptState.CAPTURED:
            fact = PaymentCapturedV1(
                payment_attempt_id=attempt_id, checkout_session_id=session_id
            )
        case AttemptState.DECLINED:
            fact = PaymentDeclinedV1(
                payment_attempt_id=attempt_id,
                checkout_session_id=session_id,
                reason=reason or "declined",
            )
        case AttemptState.EXPIRED:
            fact = PaymentExpiredV1(
                payment_attempt_id=attempt_id, checkout_session_id=session_id
            )
        case AttemptState.CANCELLED:
            fact = PaymentCancelledV1(
                payment_attempt_id=attempt_id, checkout_session_id=session_id
            )
        case AttemptState.NEEDS_PERSON:
            fact = PaymentNeedsPersonV1(
                payment_attempt_id=attempt_id,
                checkout_session_id=session_id,
                detail=reason or "",
            )
        case AttemptState.CANCELLING:
            fact = PaymentCancelWantedV1(payment_attempt_id=attempt_id)
        case AttemptState.REQUESTED:
            raise AssertionError("an attempt never returns to requested")
        case _:
            assert_never(state)
    await append_fact(conn, fact, actor, now)
    return state


async def _schedule_polls(
    conn: Connection, attempt: Attempt, account: PspAccount, now: datetime
) -> None:
    """Time only triggers: a poll account asks on its interval (one chain:
    each poll schedules the next), and a pushing account asks once when a
    QR's time is up, in case the PSP's news was lost. What the PSP then
    says decides."""
    if account.status_mode == StatusMode.POLL:
        await append_fact(
            conn,
            PaymentPollDueV1(payment_attempt_id=attempt.id),
            FLOW,
            now,
            deliver_at=now + account.poll_interval,
        )
    elif attempt.action is not None and attempt.action.expires_at is not None:
        await append_fact(
            conn,
            PaymentPollDueV1(payment_attempt_id=attempt.id),
            FLOW,
            now,
            deliver_at=attempt.action.expires_at,
        )
