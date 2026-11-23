"""Paying a quote (docs/plan-payments.md §4): the buyer's pay and
pay-another-way steps, and the effect consumers that talk to the PSP.

    pay (handler, session lock) ─▶ attempt `requested` + payment_attempt_created
    psp_payment_create  ─▶ action_required (the PSP's page or a QR)
                          | declined | the state the PSP reports
    psp_payment_capture ← payment_authorised: capture the attempt's amount
    psp_payment_cancel  ← payment_cancel_wanted: the buyer chose another way
    psp_payment_poll    ← payment_poll_due, payment_events_wanted: fetch the
                          payment's events into the inbox

After a `sent` request with no answer the operation's guard decides:
`vendor_constraint` sends again with the same key; `none` only looks the
payment up, and a payment the PSP does not show goes to a person. A
cancel and a fetch move no money and are always safe to send again.

`prepare` and `complete` run in the worker's transactions under the
attempt's row lock and never commit; `call` runs with no transaction open.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, assert_never

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor
from mor.checkout.quotes import MadeQuote, read_quote
from mor.checkout.records import SessionState
from mor.checkout.sessions import lock_session, read_address, set_session_state
from mor.config import Environment
from mor.db.connect import Connection
from mor.merchants.records import KybState
from mor.merchants.store import kyb_state_for_pay
from mor.messaging.facts import (
    Fact,
    PaymentAttemptCreatedV1,
    PaymentAuthorisedV1,
    PaymentCancelWantedV1,
    PaymentEventsWantedV1,
    PaymentPollDueV1,
)
from mor.messaging.guard import DuplicateGuard, VendorRequestState
from mor.messaging.outcome import Applied, Done, NoCall, RetryLater, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.payments.attempts import (
    FLOW,
    Attempt,
    insert_attempt,
    lock_attempt,
    move,
    record_payment,
    session_attempts,
)
from mor.payments.inbound import store_inbox
from mor.payments.transitions import OPEN_STATES, AttemptState, after_event
from mor.payments.transitions import Stale as StaleEvent
from mor.psp import registry
from mor.psp.accounts import read_psp_account, route_for
from mor.psp.port import (
    AlreadyFinal,
    CancelResult,
    CaptureResult,
    CreateResult,
    Declined,
    FetchResult,
    FindResult,
    Found,
    InboundMode,
    NotCapturable,
    Page,
    PaymentMethod,
    PaymentRequest,
    PaymentStatus,
    ProviderPayment,
    PspAccount,
    StatusMode,
)
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


# --- the buyer's steps (handlers) -----------------------------------------


@dataclass(frozen=True)
class Paying:
    attempt_id: uuid.UUID


@dataclass(frozen=True)
class AlreadyPaying:
    """A double submit, or a payment already under way or done."""


@dataclass(frozen=True)
class NotPayable:
    reason: Literal[
        "no_session",
        "not_quoted",
        "session_over",
        "quote_lapsed",
        "unknown_option",
        "no_route",
        "unknown_method",
        "merchant_not_approved",
    ]


async def pay(
    conn: Connection,
    session_id: uuid.UUID,
    option_key: str,
    method_text: str,
    return_url: str,
    actor: Actor,
    now: datetime,
) -> Paying | AlreadyPaying | NotPayable:
    """Under the session's lock: is this quote still payable? Then the
    attempt and the fact that starts it, and the session `paying`. Never
    commits; the caller commits on `Paying`, and rolls back otherwise."""
    session = await lock_session(conn, session_id)
    if session is None:
        return NotPayable("no_session")
    if session.state in (SessionState.PAYING, SessionState.COMPLETED):
        return AlreadyPaying()
    if session.state != SessionState.QUOTED or session.quote_id is None:
        return NotPayable("not_quoted")
    if now >= session.expires_at:
        return NotPayable("session_over")
    # KYB, in every environment (docs/plan-payments.md §6.1): held FOR
    # SHARE, so a suspension and this attempt cannot cross.
    if await kyb_state_for_pay(conn, session.merchant_id) != KybState.APPROVED:
        return NotPayable("merchant_not_approved")
    quote = await read_quote(conn, session.quote_id)
    assert isinstance(quote, MadeQuote), f"session {session_id} quoted without one"
    # The price held is a promise until `valid_until`, decided under the lock.
    if now >= quote.valid_until:
        return NotPayable("quote_lapsed")
    option_no: int | None = None
    for number, option in enumerate(quote.options):
        if option.key == option_key:
            option_no = number
    if option_no is None:
        return NotPayable("unknown_option")
    address = await read_address(conn, quote.address_id)
    assert address is not None
    routed = await route_for(conn, session.merchant_id, address.country, now)
    if routed is None:
        return NotPayable("no_route")
    method: PaymentMethod | None = None
    for offered in routed.account.methods:
        if offered.value == method_text:
            method = offered
    if method is None:
        return NotPayable("unknown_method")
    attempt_id = await insert_attempt(
        conn,
        session.id,
        quote.id,
        option_no,
        routed.account,
        routed.route_id,
        method,
        Money(quote.options[option_no].total_minor, quote.currency),
        return_url,
        actor,
        now,
    )
    await set_session_state(
        conn, session.id, SessionState.PAYING, session.address_id, session.quote_id
    )
    return Paying(attempt_id)


@dataclass(frozen=True)
class CancelAsked:
    pass


@dataclass(frozen=True)
class NotCancellable:
    """Nothing is waiting on the buyer: the attempt is still being made,
    or has an answer already. The page shows it as it is."""


async def pay_another_way(
    conn: Connection, session_id: uuid.UUID, actor: Actor, now: datetime
) -> CancelAsked | NotCancellable:
    """Under the open attempt's lock: only an attempt waiting on the buyer
    can be cancelled. The session stays `paying` until the PSP has
    answered, so switching methods can never charge twice. Never
    commits."""
    attempt_id: uuid.UUID | None = None
    for attempt in await session_attempts(conn, session_id):
        if attempt.state in OPEN_STATES:
            attempt_id = attempt.id
    if attempt_id is None:
        return NotCancellable()
    attempt = await lock_attempt(conn, attempt_id)
    if attempt.state != AttemptState.ACTION_REQUIRED:
        return NotCancellable()
    account = await _account(conn, attempt)
    await move(conn, attempt, AttemptState.CANCELLING, account, now, actor=actor)
    return CancelAsked()


# --- calls: what prepare asks for, and call carries out -------------------


@dataclass(frozen=True)
class CreateCall:
    account: PspAccount
    request_id: uuid.UUID
    request: PaymentRequest


@dataclass(frozen=True)
class FindCall:
    """Guard `none` after an answer was lost: look only, never resend."""

    account: PspAccount
    reference: str
    operation: Literal["create", "capture"]


@dataclass(frozen=True)
class CaptureCall:
    account: PspAccount
    request_id: uuid.UUID
    provider_payment_id: str
    amount: Money
    key: str


@dataclass(frozen=True)
class CancelCall:
    account: PspAccount
    request_id: uuid.UUID
    provider_payment_id: str


@dataclass(frozen=True)
class PollCall:
    account: PspAccount
    provider_payment_id: str


type PaymentCall = CreateCall | FindCall | CaptureCall | CancelCall | PollCall


# --- answers: a call and what the PSP said, from call to complete ---------


@dataclass(frozen=True)
class CreateAnswer:
    call: CreateCall
    result: CreateResult


@dataclass(frozen=True)
class FindAnswer:
    call: FindCall
    result: FindResult


@dataclass(frozen=True)
class CaptureAnswer:
    call: CaptureCall
    result: CaptureResult


@dataclass(frozen=True)
class CancelAnswer:
    call: CancelCall
    result: CancelResult


@dataclass(frozen=True)
class PollAnswer:
    call: PollCall
    result: FetchResult


type PaymentAnswer = (
    CreateAnswer | FindAnswer | CaptureAnswer | CancelAnswer | PollAnswer
)


async def call(
    http: httpx2.AsyncClient, environment: Environment, payment_call: PaymentCall
) -> PaymentAnswer:
    match payment_call:
        case CreateCall(account=account, request=request):
            created = await registry.create_payment(http, environment, account, request)
            return CreateAnswer(payment_call, created)
        case FindCall(account=account, reference=reference):
            found = await registry.find_payments(http, environment, account, reference)
            return FindAnswer(payment_call, found)
        case CaptureCall(
            account=account, provider_payment_id=payment_id, amount=amount, key=key
        ):
            captured = await registry.capture(
                http, environment, account, payment_id, amount, key
            )
            return CaptureAnswer(payment_call, captured)
        case CancelCall(account=account, provider_payment_id=payment_id):
            cancelled = await registry.cancel(http, environment, account, payment_id)
            return CancelAnswer(payment_call, cancelled)
        case PollCall(account=account, provider_payment_id=payment_id):
            page = await registry.fetch_events(http, environment, account, payment_id)
            return PollAnswer(payment_call, page)
        case _:
            assert_never(payment_call)


async def complete(
    conn: Connection, fact: Fact, answer: PaymentAnswer, now: datetime
) -> Done | RetryLater:
    """Every payment consumer's last step: the answer says which call it
    was, and each step checks the fact is the one its consumer gets."""
    match answer:
        case CreateAnswer():
            return await _complete_create(conn, fact, answer.call, answer.result, now)
        case FindAnswer():
            match answer.call.operation:
                case "create":
                    return await _complete_create_lookup(conn, fact, answer.result, now)
                case "capture":
                    return await _complete_capture_lookup(
                        conn, fact, answer.result, now
                    )
        case CaptureAnswer():
            return await _complete_capture(conn, fact, answer.call, answer.result, now)
        case CancelAnswer():
            return await _complete_cancel(conn, fact, answer.call, answer.result, now)
        case PollAnswer():
            return await _complete_poll(conn, fact, answer.call, answer.result, now)
        case _:
            assert_never(answer)


# --- requests -------------------------------------------------------------


type Operation = Literal["create", "capture", "cancel"]


@dataclass(frozen=True)
class _Request:
    id: uuid.UUID
    number: int
    idempotency_key: str
    state: VendorRequestState


async def _latest_request(
    conn: Connection, attempt_id: uuid.UUID, operation: Operation
) -> _Request | None:
    async with conn.cursor(row_factory=class_row(_Request)) as cur:
        await cur.execute(
            """
            SELECT id, number, idempotency_key, state FROM psp_request
            WHERE attempt_id = %s AND operation = %s
            ORDER BY number DESC LIMIT 1
            """,
            (attempt_id, operation),
        )
        return await cur.fetchone()


async def _new_request(
    conn: Connection,
    attempt_id: uuid.UUID,
    operation: Operation,
    number: int,
    now: datetime,
) -> _Request:
    request_id = uuid.uuid7()
    key = f"pay:{attempt_id}:{operation}:{number}"
    await conn.execute(
        """
        INSERT INTO psp_request
          (id, attempt_id, operation, number, idempotency_key, state, created_at)
        VALUES (%s, %s, %s, %s, %s, 'not_sent', %s)
        """,
        (request_id, attempt_id, operation, number, key, now),
    )
    return _Request(
        id=request_id,
        number=number,
        idempotency_key=key,
        state=VendorRequestState.NOT_SENT,
    )


async def _mark_sent(conn: Connection, request_id: uuid.UUID) -> None:
    """The committed "we are about to ask" mark, before any call."""
    await conn.execute(
        "UPDATE psp_request SET state = 'sent', sends = sends + 1 WHERE id = %s",
        (request_id,),
    )


async def _finish_request(
    conn: Connection,
    request_id: uuid.UUID,
    state: Literal["succeeded", "refused", "not_sent"],
    now: datetime,
    result: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE psp_request SET state = %s, result = %s, completed_at = %s
        WHERE id = %s
        """,
        (state, result, None if state == "not_sent" else now, request_id),
    )


async def _account(conn: Connection, attempt: Attempt) -> PspAccount:
    account = await read_psp_account(conn, attempt.psp_account_id)
    assert account is not None
    return account


# --- applying what the PSP says -------------------------------------------


def _event_kind(
    status: PaymentStatus,
) -> Literal["authorised", "captured", "declined", "expired", "cancelled"] | None:
    match status:
        case PaymentStatus.REQUIRES_ACTION:
            return None
        case PaymentStatus.AUTHORISED:
            return "authorised"
        case PaymentStatus.CAPTURED:
            return "captured"
        case PaymentStatus.DECLINED:
            return "declined"
        case PaymentStatus.EXPIRED:
            return "expired"
        case PaymentStatus.CANCELLED:
            return "cancelled"
        case _:
            assert_never(status)


async def apply_payment(
    conn: Connection,
    attempt: Attempt,
    account: PspAccount,
    payment: ProviderPayment,
    now: datetime,
    reason: str | None = None,
) -> Done:
    """The payment as the PSP showed it in an answer, by the same table as
    its events: an answer and a webhook racing each other end alike."""
    attempt = await record_payment(
        conn, attempt, payment.provider_payment_id, payment.action, now
    )
    kind = _event_kind(payment.status)
    if kind is None:
        if attempt.state != AttemptState.REQUESTED:
            return Done(Stale())
        await move(conn, attempt, AttemptState.ACTION_REQUIRED, account, now)
        return Done(Applied())
    target = after_event(attempt.state, kind)
    if isinstance(target, StaleEvent):
        return Done(Stale())
    await move(conn, attempt, target, account, now, reason, payment.amount)
    return Done(Applied())


async def _needs_person(
    conn: Connection, attempt: Attempt, account: PspAccount, detail: str, now: datetime
) -> Done:
    if attempt.state not in OPEN_STATES:
        return Done(Stale())
    await move(conn, attempt, AttemptState.NEEDS_PERSON, account, now, detail)
    return Done(Applied())


# --- psp_payment_create ---------------------------------------------------


def _created_id(fact: Fact) -> uuid.UUID:
    match fact:
        case PaymentAttemptCreatedV1(payment_attempt_id=attempt_id):
            return attempt_id
        case _:
            raise WrongFact(f"psp_payment_create got {fact!r}")


async def prepare_create(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | CreateCall | FindCall:
    attempt = await lock_attempt(conn, _created_id(fact))
    if attempt.state != AttemptState.REQUESTED:
        return NoCall(Stale())  # an event or a lookup answered first
    account = await _account(conn, attempt)
    request = await _latest_request(conn, attempt.id, "create")
    if request is None:
        request = await _new_request(conn, attempt.id, "create", 1, now)
    elif request.state == VendorRequestState.SENT:
        # Marked sent, no answer recorded: an earlier run died mid-call or
        # got no answer. Whether to send again is the PSP's promise.
        match account.create_guard:
            case DuplicateGuard.VENDOR_CONSTRAINT:
                pass
            case DuplicateGuard.NONE:
                return FindCall(account, str(attempt.id), "create")
            case DuplicateGuard.COMPENSABLE:
                raise AssertionError("a payment is never compensable")
            case _ as unreachable:
                assert_never(unreachable)
    elif request.state != VendorRequestState.NOT_SENT:
        raise AssertionError(f"attempt {attempt.id} requested, create {request.state}")
    await _mark_sent(conn, request.id)
    return CreateCall(
        account,
        request.id,
        PaymentRequest(
            reference=str(attempt.id),
            amount=attempt.amount,
            method=attempt.method,
            return_url=attempt.return_url,
            idempotency_key=request.idempotency_key,
        ),
    )


async def _complete_create(
    conn: Connection,
    fact: Fact,
    create: CreateCall,
    result: CreateResult,
    now: datetime,
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _created_id(fact))
    account = await _account(conn, attempt)
    match result:
        case ProviderPayment() as payment:
            await _finish_request(conn, create.request_id, "succeeded", now)
            return await apply_payment(conn, attempt, account, payment, now)
        case Declined(payment=payment, reason=reason):
            await _finish_request(conn, create.request_id, "refused", now, reason)
            return await apply_payment(conn, attempt, account, payment, now, reason)
        case Unavailable(detail=detail):
            await _finish_request(conn, create.request_id, "not_sent", now)
            return RetryLater(f"create unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"create outcome unknown: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn, attempt, account, f"create answer: {detail}", now
            )
        case _:
            assert_never(result)


async def _complete_create_lookup(
    conn: Connection, fact: Fact, result: FindResult, now: datetime
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _created_id(fact))
    account = await _account(conn, attempt)
    match result:
        case Found(payments=payments):
            if len(payments) == 0:
                # Not found proves nothing: it may still be on its way. A
                # person decides; the request stays `sent`.
                return await _needs_person(
                    conn,
                    attempt,
                    account,
                    "create answer lost; the PSP does not show it",
                    now,
                )
            if len(payments) > 1:
                return await _needs_person(
                    conn, attempt, account, "several payments at the PSP", now
                )
            request = await _latest_request(conn, attempt.id, "create")
            assert request is not None
            await _finish_request(conn, request.id, "succeeded", now)
            return await apply_payment(conn, attempt, account, payments[0], now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"create lookup: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn, attempt, account, f"lookup answer: {detail}", now
            )
        case _:
            assert_never(result)


# --- psp_payment_capture --------------------------------------------------


def _authorised_id(fact: Fact) -> uuid.UUID:
    match fact:
        case PaymentAuthorisedV1(payment_attempt_id=attempt_id):
            return attempt_id
        case _:
            raise WrongFact(f"psp_payment_capture got {fact!r}")


async def prepare_capture(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | CaptureCall | FindCall:
    """Capture at once after authorisation (answer 3 of 19-Nov-2026), but
    never once the buyer chose another way."""
    attempt = await lock_attempt(conn, _authorised_id(fact))
    if attempt.state != AttemptState.AUTHORISED:
        return NoCall(Stale())
    assert attempt.provider_payment_id is not None
    account = await _account(conn, attempt)
    request = await _latest_request(conn, attempt.id, "capture")
    if request is None:
        request = await _new_request(conn, attempt.id, "capture", 1, now)
    elif request.state == VendorRequestState.SENT:
        match account.capture_guard:
            case DuplicateGuard.VENDOR_CONSTRAINT:
                pass
            case DuplicateGuard.NONE:
                return FindCall(account, str(attempt.id), "capture")
            case DuplicateGuard.COMPENSABLE:
                raise AssertionError("a capture is never compensable")
            case _ as unreachable:
                assert_never(unreachable)
    elif request.state != VendorRequestState.NOT_SENT:
        raise AssertionError(
            f"attempt {attempt.id} authorised, capture {request.state}"
        )
    await _mark_sent(conn, request.id)
    return CaptureCall(
        account=account,
        request_id=request.id,
        provider_payment_id=attempt.provider_payment_id,
        amount=attempt.amount,
        key=request.idempotency_key,
    )


async def _complete_capture(
    conn: Connection,
    fact: Fact,
    capture: CaptureCall,
    result: CaptureResult,
    now: datetime,
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _authorised_id(fact))
    account = await _account(conn, attempt)
    match result:
        case ProviderPayment() as payment:
            await _finish_request(conn, capture.request_id, "succeeded", now)
            return await apply_payment(conn, attempt, account, payment, now)
        case NotCapturable(payment=payment):
            await _finish_request(conn, capture.request_id, "refused", now)
            if payment.status == PaymentStatus.AUTHORISED:
                return await _needs_person(
                    conn, attempt, account, "capture refused", now
                )
            return await apply_payment(conn, attempt, account, payment, now)
        case Unavailable(detail=detail):
            await _finish_request(conn, capture.request_id, "not_sent", now)
            return RetryLater(f"capture unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"capture outcome unknown: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn, attempt, account, f"capture answer: {detail}", now
            )
        case _:
            assert_never(result)


async def _complete_capture_lookup(
    conn: Connection, fact: Fact, result: FindResult, now: datetime
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _authorised_id(fact))
    account = await _account(conn, attempt)
    match result:
        case Found(payments=payments):
            for payment in payments:
                if payment.provider_payment_id != attempt.provider_payment_id:
                    continue
                if payment.status == PaymentStatus.AUTHORISED:
                    # Still authorised proves nothing: the capture may be on
                    # its way. A person decides.
                    return await _needs_person(
                        conn,
                        attempt,
                        account,
                        "capture answer lost; the PSP shows it authorised",
                        now,
                    )
                request = await _latest_request(conn, attempt.id, "capture")
                assert request is not None
                await _finish_request(conn, request.id, "succeeded", now)
                return await apply_payment(conn, attempt, account, payment, now)
            return await _needs_person(
                conn, attempt, account, "the PSP does not show the payment", now
            )
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"capture lookup: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn, attempt, account, f"lookup answer: {detail}", now
            )
        case _:
            assert_never(result)


# --- psp_payment_cancel ---------------------------------------------------


def _cancel_id(fact: Fact) -> uuid.UUID:
    match fact:
        case PaymentCancelWantedV1(payment_attempt_id=attempt_id):
            return attempt_id
        case _:
            raise WrongFact(f"psp_payment_cancel got {fact!r}")


async def prepare_cancel(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | CancelCall:
    """A cancel moves no money and cancelling twice leaves it cancelled, so
    it is always sent again after a lost answer."""
    attempt = await lock_attempt(conn, _cancel_id(fact))
    if attempt.state != AttemptState.CANCELLING:
        return NoCall(Stale())
    assert attempt.provider_payment_id is not None
    account = await _account(conn, attempt)
    request = await _latest_request(conn, attempt.id, "cancel")
    if request is None or request.state not in ("not_sent", "sent"):
        number = 1 if request is None else request.number + 1
        request = await _new_request(conn, attempt.id, "cancel", number, now)
    await _mark_sent(conn, request.id)
    return CancelCall(account, request.id, attempt.provider_payment_id)


async def _complete_cancel(
    conn: Connection,
    fact: Fact,
    cancel: CancelCall,
    result: CancelResult,
    now: datetime,
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _cancel_id(fact))
    account = await _account(conn, attempt)
    match result:
        case ProviderPayment() as payment:
            await _finish_request(conn, cancel.request_id, "succeeded", now)
            return await apply_payment(conn, attempt, account, payment, now)
        case AlreadyFinal(payment=payment):
            # Too late: the payment ended another way, perhaps captured,
            # which is honoured.
            await _finish_request(conn, cancel.request_id, "refused", now)
            return await apply_payment(conn, attempt, account, payment, now)
        case Unavailable(detail=detail):
            await _finish_request(conn, cancel.request_id, "not_sent", now)
            return RetryLater(f"cancel unavailable: {detail}")
        case OutcomeUnknown(detail=detail):
            return RetryLater(f"cancel outcome unknown: {detail}")
        case UndecodableBody(detail=detail):
            return await _needs_person(
                conn, attempt, account, f"cancel answer: {detail}", now
            )
        case _:
            assert_never(result)


# --- psp_payment_poll -----------------------------------------------------


def _poll_id(fact: Fact) -> uuid.UUID:
    match fact:
        case (
            PaymentPollDueV1(payment_attempt_id=attempt_id)
            | PaymentEventsWantedV1(payment_attempt_id=attempt_id)
        ):
            return attempt_id
        case _:
            raise WrongFact(f"psp_payment_poll got {fact!r}")


async def prepare_poll(
    conn: Connection, fact: Fact, now: datetime
) -> NoCall | PollCall:
    """A fetch only reads at the PSP: no request row, no guard. Not once
    the attempt has an end, whatever started it."""
    attempt = await lock_attempt(conn, _poll_id(fact))
    if attempt.state not in OPEN_STATES or attempt.provider_payment_id is None:
        return NoCall(Stale())
    account = await _account(conn, attempt)
    return PollCall(account, attempt.provider_payment_id)


async def _complete_poll(
    conn: Connection,
    fact: Fact,
    poll: PollCall,
    result: FetchResult,
    now: datetime,
) -> Done | RetryLater:
    attempt = await lock_attempt(conn, _poll_id(fact))
    account = poll.account
    match result:
        case Page(body=body):
            # Stored raw and decoded on the webhook's path.
            await store_inbox(conn, account.id, InboundMode.POLL, body, now)
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            return RetryLater(f"poll: {detail}")
    match fact:
        case PaymentPollDueV1() if (
            account.status_mode == StatusMode.POLL and attempt.state in OPEN_STATES
        ):
            # The page just stored is not applied yet, so an end in it is
            # seen by the next poll, which then stops.
            await append_fact(
                conn,
                PaymentPollDueV1(payment_attempt_id=attempt.id),
                FLOW,
                now,
                deliver_at=now + account.poll_interval,
            )
        case _:
            pass  # a QR's expiry or a thin webhook's fetch schedules nothing
    return Done(Applied())
