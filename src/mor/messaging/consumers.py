"""The table: each consumer is plain functions, picked by an exhaustive
`match` on its name. The kind is in the consumer's type
(`mor.messaging.routing`), so neither kind can be asked for the other's.

Being reworked (plan-messaging-rework.md §3.3): each case narrows the
fact to what the consumer needs (`expect`), and an effect consumer's three
steps run through `run_steps` (the cases of `run_effect`). The others
still go through `prepare`, `call` and `complete` below, where
`call` returns an answer record holding the call and what the vendor said
to it, so `complete` is picked by the answer alone."""

from dataclasses import dataclass
from datetime import datetime

import httpx2

import mor.couriers.prices
import mor.duties.lookups
import mor.payments.flow
import mor.settlement.fetch
from mor.checkout import flow as checkout_flow
from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import flow as echo_flow
from mor.echo import inbound as echo_inbound
from mor.echo import reports as echo_reports
from mor.echo import tracking as echo_tracking
from mor.invoices import store as invoice_store
from mor.messaging.delivery import (
    MAX_EFFECT_STEPS,
    Candidate,
    Deferred,
    Ran,
    Skipped,
    defer_delivery,
    finish_delivery,
    open_delivery,
    run_steps,
)
from mor.messaging.facts import (
    DutyRateNeededV1,
    Fact,
    PaymentAttemptCreatedV1,
    PaymentAuthorisedV1,
    PaymentCancelWantedV1,
    PaymentEventsWantedV1,
    PaymentPollDueV1,
    PspSettlementDueV1,
    ShippingPriceNeededV1,
)
from mor.messaging.outcome import (
    Again,
    ConsumerOutcome,
    Done,
    NoCall,
    RetryLater,
    StepResult,
)
from mor.messaging.routing import (
    ConsumerName,
    EffectConsumer,
    LocalConsumer,
    WrongFact,
)
from mor.orders import flow as order_flow
from mor.payments import inbound as payment_inbound
from mor.probe import flow as probe_flow
from mor.settlement import store as settlement_store


type EffectCall = (
    echo_flow.EchoCall | echo_tracking.FetchEventsCall | echo_reports.ReportCall
)
type EffectAnswer = (
    echo_flow.EchoAnswer | echo_tracking.FetchEventsAnswer | echo_reports.ReportAnswer
)


def expect[F](fact: Fact, record: type[F]) -> F:
    """The fact as the one record its consumer gets; any other is a bug in
    routing."""
    if not isinstance(fact, record):
        raise WrongFact(f"expected {record.__name__}, got {fact!r}")
    return fact


async def run_effect(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    consumer: EffectConsumer,
    fact: Fact,
    now: datetime,
) -> Ran | Skipped | Deferred:
    """An effect consumer's whole run, its delivery opened and its
    aggregate's advisory lock held by the worker."""
    match consumer:
        case ConsumerName.DUTY_RATE_ASK:
            lookup_id = expect(fact, DutyRateNeededV1).duty_lookup_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                lookup_id,
                now,
                prepare=mor.duties.lookups.prepare_ask,
                call=mor.duties.lookups.ask,
                complete=mor.duties.lookups.complete_ask,
            )
        case ConsumerName.SHIPPING_PRICE_ASK:
            lookup_id = expect(fact, ShippingPriceNeededV1).shipping_price_lookup_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                lookup_id,
                now,
                prepare=mor.couriers.prices.prepare_rate,
                call=mor.couriers.prices.ask_rate,
                complete=mor.couriers.prices.complete_rate,
            )
        case ConsumerName.PSP_PAYMENT_CREATE:
            attempt_id = expect(fact, PaymentAttemptCreatedV1).payment_attempt_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                attempt_id,
                now,
                prepare=mor.payments.flow.prepare_create,
                call=mor.payments.flow.send_create,
                complete=mor.payments.flow.complete_create,
            )
        case ConsumerName.PSP_PAYMENT_CAPTURE:
            attempt_id = expect(fact, PaymentAuthorisedV1).payment_attempt_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                attempt_id,
                now,
                prepare=mor.payments.flow.prepare_capture,
                call=mor.payments.flow.send_capture,
                complete=mor.payments.flow.complete_capture,
            )
        case ConsumerName.PSP_PAYMENT_CANCEL:
            attempt_id = expect(fact, PaymentCancelWantedV1).payment_attempt_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                attempt_id,
                now,
                prepare=mor.payments.flow.prepare_cancel,
                call=mor.payments.flow.send_cancel,
                complete=mor.payments.flow.complete_cancel,
            )
        case ConsumerName.PSP_PAYMENT_POLL:
            attempt_id = expect(fact, PaymentPollDueV1).payment_attempt_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                attempt_id,
                now,
                prepare=mor.payments.flow.prepare_poll,
                call=mor.payments.flow.fetch_events,
                complete=mor.payments.flow.complete_poll,
            )
        case ConsumerName.PSP_PAYMENT_FETCH:
            attempt_id = expect(fact, PaymentEventsWantedV1).payment_attempt_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                attempt_id,
                now,
                prepare=mor.payments.flow.prepare_poll,
                call=mor.payments.flow.fetch_events,
                complete=mor.payments.flow.complete_fetch,
            )
        case ConsumerName.PSP_SETTLEMENT_FETCH:
            account_id = expect(fact, PspSettlementDueV1).psp_account_id
            return await run_steps(
                conn,
                http,
                environment,
                candidate,
                account_id,
                now,
                prepare=mor.settlement.fetch.prepare_fetch,
                call=mor.settlement.fetch.read_settlement,
                complete=mor.settlement.fetch.complete_fetch,
            )
        # Transitional: the consumers not converted to run_steps yet. Goes
        # once they all are (plan-messaging-rework.md §3.3); no prod data
        # depends on it.
        case _:
            return await _run_unconverted(
                conn, http, environment, candidate, consumer, now
            )


async def handle(
    conn: Connection,
    consumer: LocalConsumer,
    fact: Fact,
    environment: Environment,
    now: datetime,
) -> ConsumerOutcome:
    """A local consumer, in the worker's transaction. Never commits."""
    match consumer:
        case ConsumerName.PROBE_ECHO:
            return await probe_flow.echo(conn, fact, now)
        case ConsumerName.PROBE_FINISH:
            return await probe_flow.finish(conn, fact, now)
        case ConsumerName.ECHO_INBOX_DECODE:
            return await echo_inbound.decode_inbox(conn, fact, now)
        case ConsumerName.ECHO_EVENT_APPLY:
            return await echo_inbound.apply_event(conn, fact, now)
        case ConsumerName.ECHO_TRACKING_START:
            return await echo_tracking.start_tracking(conn, fact, now)
        case ConsumerName.QUOTE_WAIT_NOTIFY:
            return await checkout_flow.notify_waiting(conn, fact, now)
        case ConsumerName.QUOTE_BUILD:
            return await checkout_flow.build_waiting(conn, fact, environment, now)
        case ConsumerName.CHECKOUT_SESSION_EXPIRE:
            return await checkout_flow.expire_session(conn, fact, now)
        case ConsumerName.PSP_INBOX_DECODE:
            return await payment_inbound.decode_inbox(conn, fact, now)
        case ConsumerName.PAYMENT_EVENT_APPLY:
            return await payment_inbound.apply_event(conn, fact, now)
        case ConsumerName.ORDER_CREATE:
            return await order_flow.create_order(conn, fact, now)
        case ConsumerName.INVOICE_ISSUE:
            return await invoice_store.issue_invoice(conn, fact, now)
        case ConsumerName.SESSION_PAYMENT_ENDED:
            return await checkout_flow.end_payment(conn, fact, now)
        case ConsumerName.SETTLEMENT_LINE_BOOK:
            return await settlement_store.book_lines(conn, fact, now)
        case ConsumerName.SETTLEMENT_PAYOUT_BOOK:
            return await settlement_store.book_payout(conn, fact, now)
        case ConsumerName.SETTLEMENT_PAYOUT_RECONCILE:
            return await settlement_store.reconcile_payout(conn, fact, now)


async def prepare(
    conn: Connection, consumer: EffectConsumer, fact: Fact, now: datetime
) -> NoCall | EffectCall:
    """An effect consumer's first step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            return await echo_flow.prepare(conn, fact, now)
        case ConsumerName.ECHO_POLL:
            return await echo_tracking.prepare_poll(conn, fact, now)
        case ConsumerName.ECHO_REPORT_FETCH:
            return await echo_reports.prepare_reports(conn, fact, now)
        case ConsumerName.DUTY_RATE_ASK:
            raise AssertionError("duty_rate_ask runs through run_steps")
        case ConsumerName.SHIPPING_PRICE_ASK:
            raise AssertionError("shipping_price_ask runs through run_steps")
        case (
            ConsumerName.PSP_PAYMENT_CREATE
            | ConsumerName.PSP_PAYMENT_CAPTURE
            | ConsumerName.PSP_PAYMENT_CANCEL
            | ConsumerName.PSP_PAYMENT_POLL
            | ConsumerName.PSP_PAYMENT_FETCH
        ):
            raise AssertionError(f"{consumer} runs through run_steps")
        case ConsumerName.PSP_SETTLEMENT_FETCH:
            raise AssertionError("psp_settlement_fetch runs through run_steps")


async def call(
    http: httpx2.AsyncClient, environment: Environment, effect_call: EffectCall
) -> EffectAnswer:
    """The outside call, with no transaction open. The call record says
    which port it is for."""
    match effect_call:
        case echo_flow.SendCall() | echo_flow.FindCall() | echo_flow.CancelCall():
            return await echo_flow.call(http, environment, effect_call)
        case echo_tracking.FetchEventsCall():
            return await echo_tracking.call_poll(http, environment, effect_call)
        case echo_reports.ListReportsCall() | echo_reports.FetchReportCall():
            return await echo_reports.call_reports(http, environment, effect_call)


async def complete(
    conn: Connection, fact: Fact, answer: EffectAnswer, now: datetime
) -> StepResult:
    """An effect consumer's last step, in its own transaction. The answer
    says which module asked; that module checks the fact is its own."""
    match answer:
        case echo_flow.SendAnswer() | echo_flow.FindAnswer() | echo_flow.CancelAnswer():
            return await echo_flow.complete(conn, fact, answer, now)
        case echo_tracking.FetchEventsAnswer():
            return await echo_tracking.complete_poll(conn, fact, answer, now)
        case echo_reports.ListReportsAnswer() | echo_reports.FetchReportAnswer():
            return await echo_reports.complete_reports(conn, fact, answer, now)


# --- transitional: the effect driver for consumers not on run_steps yet ----
# Goes with `prepare`, `call` and `complete` above once every effect
# consumer runs through run_steps (plan-messaging-rework.md §3.3).


@dataclass(frozen=True)
class Prepared:
    """An effect consumer's `prepare` committed a call to make."""

    fact: Fact
    call: EffectCall


async def prepare_delivery(
    conn: Connection, candidate: Candidate, consumer: EffectConsumer, now: datetime
) -> Prepared | Ran | Skipped:
    """An effect consumer's first transaction. Never commits."""
    fact = await open_delivery(conn, candidate, now)
    if isinstance(fact, Skipped):
        return fact
    prepared = await prepare(conn, consumer, fact, now)
    if isinstance(prepared, NoCall):
        await finish_delivery(conn, candidate, now)
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
    step = await complete(conn, fact, answer, now)
    match step:
        case Done(outcome=outcome):
            await finish_delivery(conn, candidate, now)
            return Ran(outcome)
        case RetryLater(reason=reason):
            not_before = await defer_delivery(conn, candidate, reason, now)
            return Deferred(reason, not_before)
        case Again():
            return step


async def _run_unconverted(
    conn: Connection,
    http: httpx2.AsyncClient,
    environment: Environment,
    candidate: Candidate,
    consumer: EffectConsumer,
    now: datetime,
) -> Ran | Skipped | Deferred:
    """prepare (commit), call with no transaction open, complete (commit),
    again while `complete` asks for it."""
    for _ in range(MAX_EFFECT_STEPS):
        async with conn.transaction():
            prepared = await prepare_delivery(conn, candidate, consumer, now)
        if not isinstance(prepared, Prepared):
            return prepared
        answer = await call(http, environment, prepared.call)
        async with conn.transaction():
            completed = await complete_delivery(
                conn, candidate, prepared.fact, answer, now
            )
        if not isinstance(completed, Again):
            return completed
    raise AssertionError(f"{candidate} did not finish in {MAX_EFFECT_STEPS} steps")
