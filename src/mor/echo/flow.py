"""The echo flow: one effect consumer, `echo_send`, on echo_job_requested.

Transitional (docs/plan-messaging.md §10): it proves prepare / call /
complete and the three duplicate guards, and goes once the fake PSP
exercises the same machinery (slice 5), unless it serves something else.

    requested ──send──▶ echoed | refused
        │  (a `sent` request with no result: the guard decides)
        ├─ vendor_constraint: send again, same key
        ├─ compensable: send again, then ─▶ deduplicating ─find, cancel extras─▶
        │                                   echoed | refused
        └─ none: find by reference ─▶ echoed | needs_person

`prepare` and `complete` run inside the worker's transactions under the
job's row lock and never commit; `call` runs with no transaction open.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_row, one_value
from mor.echo import registry
from mor.echo.capabilities import send_guard
from mor.echo.port import (
    Cancelled,
    CancelResult,
    EchoAccount,
    EchoProvider,
    EventMode,
    FindResult,
    Found,
    Refused,
    SendRequest,
    SendResult,
    Sent,
)
from mor.echo.records import EchoJobState, EchoOperation
from mor.messaging.facts import (
    EchoJobEchoedV1,
    EchoJobNeedsPersonV1,
    EchoJobRefusedV1,
    EchoJobRequestedV1,
    Fact,
)
from mor.messaging.guard import DuplicateGuard, VendorRequestState
from mor.messaging.outcome import Again, Applied, Done, NoCall, RetryLater, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.provider_accounts import ProviderAccountKind
from mor.vendor_failures import OutcomeUnknown, Unavailable


ACTOR = Actor(ActorKind.SYSTEM, "echo_send")


# --- calls: what prepare asks for, and call carries out -------------------


@dataclass(frozen=True)
class SendCall:
    account: EchoAccount
    request_id: uuid.UUID
    request: SendRequest


@dataclass(frozen=True)
class FindCall:
    account: EchoAccount
    reference: str


@dataclass(frozen=True)
class CancelCall:
    account: EchoAccount
    request_id: uuid.UUID
    echo_id: str


type EchoCall = SendCall | FindCall | CancelCall


# --- answers: a call and what the vendor said, from call to complete ------


@dataclass(frozen=True)
class SendAnswer:
    call: SendCall
    result: SendResult


@dataclass(frozen=True)
class FindAnswer:
    call: FindCall
    result: FindResult


@dataclass(frozen=True)
class CancelAnswer:
    call: CancelCall
    result: CancelResult


type EchoAnswer = SendAnswer | FindAnswer | CancelAnswer


# --- rows -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _JobRow:
    id: uuid.UUID
    account_id: uuid.UUID
    message: str
    state: EchoJobState
    vendor_echo_id: str | None
    refusal: str | None


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: uuid.UUID
    provider: EchoProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int


@dataclass(frozen=True, kw_only=True)
class _RequestRow:
    id: uuid.UUID
    number: int
    idempotency_key: str
    state: VendorRequestState
    sends: int
    target_echo_id: str | None


async def create_echo_account(
    conn: Connection,
    provider: EchoProvider,
    base_url: str,
    timeout: timedelta,
    event_mode: EventMode = EventMode.NONE,
    webhook_secret: str = "",
    poll_interval: timedelta = timedelta(minutes=5),
) -> uuid.UUID:
    """Every echo provider is a fake, so every echo account is too."""
    account_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO echo_account
          (id, provider, kind, base_url, timeout_ms, event_mode, webhook_secret,
           poll_interval_s)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            account_id,
            provider,
            ProviderAccountKind.FAKE,
            base_url,
            int(timeout.total_seconds() * 1000),
            event_mode,
            webhook_secret,
            int(poll_interval.total_seconds()),
        ),
    )
    return account_id


async def create_echo_job(
    conn: Connection, account_id: uuid.UUID, message: str, actor: Actor, now: datetime
) -> uuid.UUID:
    job_id = uuid.uuid7()
    await conn.execute(
        """
        INSERT INTO echo_job (id, account_id, message, state, created_at, updated_at)
        VALUES (%s, %s, %s, 'requested', %s, %s)
        """,
        (job_id, account_id, message, now, now),
    )
    await append_fact(conn, EchoJobRequestedV1(echo_job_id=job_id), actor, now)
    return job_id


async def echo_job_state(conn: Connection, job_id: uuid.UUID) -> EchoJobState:
    return await one_value(
        conn, "SELECT state FROM echo_job WHERE id = %s", (job_id,), EchoJobState
    )


async def _lock_job(conn: Connection, job_id: uuid.UUID) -> _JobRow:
    async with conn.cursor(row_factory=class_row(_JobRow)) as cur:
        await cur.execute(
            """
            SELECT id, account_id, message, state, vendor_echo_id, refusal
            FROM echo_job WHERE id = %s FOR UPDATE
            """,
            (job_id,),
        )
        job = await cur.fetchone()
    if job is None:
        raise LookupError(f"echo job {job_id} does not exist")
    return job


async def _account(conn: Connection, account_id: uuid.UUID) -> EchoAccount:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            "SELECT id, provider, kind, base_url, timeout_ms FROM echo_account"
            " WHERE id = %s",
            (account_id,),
        )
        row = await one_row(cur, f"echo account {account_id}")
    return EchoAccount(
        id=row.id,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
    )


async def _latest_request(
    conn: Connection, job_id: uuid.UUID, operation: EchoOperation
) -> _RequestRow | None:
    async with conn.cursor(row_factory=class_row(_RequestRow)) as cur:
        await cur.execute(
            """
            SELECT id, number, idempotency_key, state, sends, target_echo_id
            FROM echo_request
            WHERE echo_job_id = %s AND operation = %s
            ORDER BY number DESC LIMIT 1
            """,
            (job_id, operation),
        )
        return await cur.fetchone()


async def _new_request(
    conn: Connection,
    job_id: uuid.UUID,
    operation: EchoOperation,
    number: int,
    target_echo_id: str | None,
    now: datetime,
) -> _RequestRow:
    request_id = uuid.uuid7()
    key = f"echo:{job_id}:{operation}:{number}"
    await conn.execute(
        """
        INSERT INTO echo_request
          (id, echo_job_id, operation, number, idempotency_key, state,
           target_echo_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            request_id,
            job_id,
            operation,
            number,
            key,
            VendorRequestState.NOT_SENT,
            target_echo_id,
            now,
        ),
    )
    return _RequestRow(
        id=request_id,
        number=number,
        idempotency_key=key,
        state=VendorRequestState.NOT_SENT,
        sends=0,
        target_echo_id=target_echo_id,
    )


async def _mark_sent(conn: Connection, request: _RequestRow) -> None:
    """The committed "we are about to ask" mark, before any call."""
    await conn.execute(
        "UPDATE echo_request SET state = %s, sends = sends + 1 WHERE id = %s",
        (VendorRequestState.SENT, request.id),
    )


async def _finish_request(
    conn: Connection,
    request_id: uuid.UUID,
    state: Literal[VendorRequestState.SUCCEEDED, VendorRequestState.REFUSED],
    now: datetime,
    result_echo_id: str | None = None,
    refusal: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE echo_request
        SET state = %s, result_echo_id = %s, refusal = %s, completed_at = %s
        WHERE id = %s
        """,
        (
            state,
            result_echo_id,
            refusal,
            now,
            request_id,
        ),
    )


async def _unsend_request(conn: Connection, request_id: uuid.UUID) -> None:
    """The vendor said the call did nothing (Unavailable): the request goes
    back to `not_sent`, and the retry sends it with the same key."""
    await conn.execute(
        "UPDATE echo_request SET state = %s WHERE id = %s",
        (VendorRequestState.NOT_SENT, request_id),
    )


async def _set_job(
    conn: Connection,
    job_id: uuid.UUID,
    state: EchoJobState,
    now: datetime,
    vendor_echo_id: str | None = None,
    refusal: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE echo_job
        SET state = %s, vendor_echo_id = %s, refusal = %s, updated_at = %s
        WHERE id = %s
        """,
        (state, vendor_echo_id, refusal, now, job_id),
    )


def _job_id(fact: Fact) -> uuid.UUID:
    match fact:
        case EchoJobRequestedV1(echo_job_id=job_id):
            return job_id
        case _:
            raise WrongFact(f"echo_send got {fact!r}")


# --- prepare --------------------------------------------------------------


async def prepare(conn: Connection, fact: Fact, now: datetime) -> NoCall | EchoCall:
    job = await _lock_job(conn, _job_id(fact))
    account = await _account(conn, job.account_id)
    match job.state:
        case EchoJobState.ECHOED | EchoJobState.REFUSED | EchoJobState.NEEDS_PERSON:
            return NoCall(Stale())
        case EchoJobState.DEDUPLICATING:
            cancel = await _latest_request(conn, job.id, EchoOperation.CANCEL)
            if cancel is not None and cancel.state in (
                VendorRequestState.NOT_SENT,
                VendorRequestState.SENT,
            ):
                # Cancelling twice leaves an echo cancelled: always resend.
                assert cancel.target_echo_id is not None
                await _mark_sent(conn, cancel)
                return CancelCall(account, cancel.id, cancel.target_echo_id)
            return FindCall(account, str(job.id))
        case EchoJobState.REQUESTED:
            return await _prepare_send(conn, job, account, now)


async def _prepare_send(
    conn: Connection, job: _JobRow, account: EchoAccount, now: datetime
) -> EchoCall:
    send = await _latest_request(conn, job.id, EchoOperation.SEND)
    if send is None:
        send = await _new_request(conn, job.id, EchoOperation.SEND, 1, None, now)
    match send.state:
        case VendorRequestState.NOT_SENT:
            # New, or the vendor said an earlier send did nothing.
            pass
        case VendorRequestState.SENT:
            # Marked sent, no result recorded: an earlier run died mid-call
            # or got no answer. Whether to send again is the vendor's
            # promise.
            match send_guard(account.provider):
                case DuplicateGuard.VENDOR_CONSTRAINT | DuplicateGuard.COMPENSABLE:
                    pass
                case DuplicateGuard.NONE:
                    return FindCall(account, str(job.id))
        case VendorRequestState.SUCCEEDED | VendorRequestState.REFUSED:
            # An answer moves the job past `requested` in the same
            # transaction that finishes the request.
            raise AssertionError(f"job {job.id} requested but send is {send.state}")
    await _mark_sent(conn, send)
    request = SendRequest(str(job.id), job.message, send.idempotency_key)
    return SendCall(account, send.id, request)


# --- call -----------------------------------------------------------------


async def call(
    http: httpx2.AsyncClient, environment: Environment, echo_call: EchoCall
) -> EchoAnswer:
    match echo_call:
        case SendCall(account=account, request=request):
            sent = await registry.send(http, environment, account, request)
            return SendAnswer(echo_call, sent)
        case FindCall(account=account, reference=reference):
            found = await registry.find_by_reference(
                http, environment, account, reference
            )
            return FindAnswer(echo_call, found)
        case CancelCall(account=account, echo_id=echo_id):
            cancelled = await registry.cancel(http, environment, account, echo_id)
            return CancelAnswer(echo_call, cancelled)


# --- complete -------------------------------------------------------------


async def complete(
    conn: Connection, fact: Fact, answer: EchoAnswer, now: datetime
) -> Done | RetryLater | Again:
    job = await _lock_job(conn, _job_id(fact))
    state = job.state
    match answer:
        case SendAnswer():
            assert state == EchoJobState.REQUESTED, state
            return await _complete_send(conn, job, answer.call, answer.result, now)
        case FindAnswer():
            match state:
                case EchoJobState.REQUESTED:
                    return await _complete_lookup(conn, job, answer.result, now)
                case EchoJobState.DEDUPLICATING:
                    return await _complete_dedupe_find(conn, job, answer.result, now)
                case _:
                    raise AssertionError(f"find completed for a job {state}")
        case CancelAnswer():
            assert state == EchoJobState.DEDUPLICATING, state
            return await _complete_cancel(conn, answer.call, answer.result, now)


async def _echoed(
    conn: Connection, job_id: uuid.UUID, echo_id: str, now: datetime
) -> Done:
    await _set_job(conn, job_id, EchoJobState.ECHOED, now, vendor_echo_id=echo_id)
    await append_fact(
        conn, EchoJobEchoedV1(echo_job_id=job_id, vendor_echo_id=echo_id), ACTOR, now
    )
    return Done(Applied())


async def _refused(
    conn: Connection, job_id: uuid.UUID, reason: str, now: datetime
) -> Done:
    await _set_job(conn, job_id, EchoJobState.REFUSED, now, refusal=reason)
    await append_fact(
        conn, EchoJobRefusedV1(echo_job_id=job_id, reason=reason), ACTOR, now
    )
    return Done(Applied())


async def _complete_send(
    conn: Connection, job: _JobRow, send: SendCall, result: SendResult, now: datetime
) -> Done | RetryLater | Again:
    request = await _latest_request(conn, job.id, EchoOperation.SEND)
    assert request is not None and request.id == send.request_id
    # Sent more than once on a vendor that may have made a duplicate: look
    # before deciding, whatever this answer was.
    may_duplicate = (
        request.sends > 1
        and send_guard(send.account.provider) == DuplicateGuard.COMPENSABLE
    )
    match result:
        case Sent(echo_id=echo_id):
            await _finish_request(
                conn,
                request.id,
                VendorRequestState.SUCCEEDED,
                now,
                result_echo_id=echo_id,
            )
            if may_duplicate:
                await _set_job(
                    conn,
                    job.id,
                    EchoJobState.DEDUPLICATING,
                    now,
                    vendor_echo_id=echo_id,
                )
                return Again()
            return await _echoed(conn, job.id, echo_id, now)
        case Refused(reason=reason):
            await _finish_request(
                conn, request.id, VendorRequestState.REFUSED, now, refusal=reason
            )
            if may_duplicate:
                # An earlier send may still have made an echo: keep it if so.
                await _set_job(
                    conn, job.id, EchoJobState.DEDUPLICATING, now, refusal=reason
                )
                return Again()
            return await _refused(conn, job.id, reason, now)
        case Unavailable(detail=detail):
            await _unsend_request(conn, request.id)
            return RetryLater(f"unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"outcome unknown: {detail}")


async def _complete_lookup(
    conn: Connection, job: _JobRow, result: FindResult, now: datetime
) -> Done | RetryLater:
    """Guard `none`: never resent, so the vendor holds at most one echo."""
    match result:
        case Found(echoes=echoes):
            assert len(echoes) <= 1, f"two echoes for {job.id} on a none guard"
            request = await _latest_request(conn, job.id, EchoOperation.SEND)
            assert request is not None
            if len(echoes) == 0:
                # Not found proves nothing: it may still be on its way. A
                # person decides; the request stays `sent`.
                await _set_job(conn, job.id, EchoJobState.NEEDS_PERSON, now)
                await append_fact(
                    conn, EchoJobNeedsPersonV1(echo_job_id=job.id), ACTOR, now
                )
                return Done(Applied())
            echo_id = echoes[0].echo_id
            await _finish_request(
                conn,
                request.id,
                VendorRequestState.SUCCEEDED,
                now,
                result_echo_id=echo_id,
            )
            return await _echoed(conn, job.id, echo_id, now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"lookup: {detail}")


async def _complete_dedupe_find(
    conn: Connection, job: _JobRow, result: FindResult, now: datetime
) -> Done | RetryLater | Again:
    match result:
        case Found(echoes=echoes):
            live: list[str] = []
            for echo in echoes:
                if echo.state == "live":
                    live.append(echo.echo_id)
            keep = job.vendor_echo_id
            if keep is None and live:
                keep = live[0]
            extras: list[str] = []
            for echo_id in live:
                if echo_id != keep:
                    extras.append(echo_id)
            if extras:
                last = await _latest_request(conn, job.id, EchoOperation.CANCEL)
                number = 1 if last is None else last.number + 1
                await _new_request(
                    conn, job.id, EchoOperation.CANCEL, number, extras[0], now
                )
                return Again()
            if keep is not None:
                return await _echoed(conn, job.id, keep, now)
            assert job.refusal is not None
            return await _refused(conn, job.id, job.refusal, now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"dedupe lookup: {detail}")


async def _complete_cancel(
    conn: Connection, cancel: CancelCall, result: CancelResult, now: datetime
) -> Done | RetryLater | Again:
    match result:
        case Cancelled():
            await _finish_request(
                conn, cancel.request_id, VendorRequestState.SUCCEEDED, now
            )
            return Again()
        case Unavailable(detail=detail):
            await _unsend_request(conn, cancel.request_id)
            return RetryLater(f"cancel unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"cancel outcome unknown: {detail}")
        case Refused(reason=reason):
            raise AssertionError(f"cancel of {cancel.echo_id} refused: {reason}")
