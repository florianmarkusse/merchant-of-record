"""The dispatch tables: each consumer is plain functions, picked by an
exhaustive `match`. A local consumer has `handle`; an effect consumer has
`prepare`, `call` and `complete` (plan-architecture.md §5). The kind is in
the consumer's type (`mor.messaging.routing`), so neither table can be
asked for a consumer of the other kind.

`call` returns an answer record holding the call and what the vendor said
to it, so `complete` is picked by the answer alone, and each module's
`complete` matches exactly the results its call can get."""

from datetime import datetime

import httpx2

from mor.checkout import flow as checkout_flow
from mor.config import Environment
from mor.couriers import prices as courier_prices
from mor.db.connect import Connection
from mor.duties import lookups as duty_lookups
from mor.echo import flow as echo_flow
from mor.echo import inbound as echo_inbound
from mor.echo import reports as echo_reports
from mor.echo import tracking as echo_tracking
from mor.invoices import store as invoice_store
from mor.messaging.facts import Fact
from mor.messaging.outcome import ConsumerOutcome, NoCall, StepResult
from mor.messaging.routing import ConsumerName, EffectConsumer, LocalConsumer
from mor.orders import flow as order_flow
from mor.payments import flow as payment_flow
from mor.payments import inbound as payment_inbound
from mor.probe import flow as probe_flow
from mor.settlement import fetch as settlement_fetch
from mor.settlement import store as settlement_store


type EffectCall = (
    echo_flow.EchoCall
    | echo_tracking.FetchEventsCall
    | echo_reports.ReportCall
    | duty_lookups.AskCall
    | courier_prices.PriceCall
    | payment_flow.PaymentCall
    | settlement_fetch.SettlementCall
)
type EffectAnswer = (
    echo_flow.EchoAnswer
    | echo_tracking.FetchEventsAnswer
    | echo_reports.ReportAnswer
    | duty_lookups.AskAnswer
    | courier_prices.PriceAnswer
    | payment_flow.PaymentAnswer
    | settlement_fetch.SettlementAnswer
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
            return await duty_lookups.prepare(conn, fact, now)
        case ConsumerName.SHIPPING_PRICE_ASK:
            return await courier_prices.prepare(conn, fact, now)
        case ConsumerName.PSP_PAYMENT_CREATE:
            return await payment_flow.prepare_create(conn, fact, now)
        case ConsumerName.PSP_PAYMENT_CAPTURE:
            return await payment_flow.prepare_capture(conn, fact, now)
        case ConsumerName.PSP_PAYMENT_CANCEL:
            return await payment_flow.prepare_cancel(conn, fact, now)
        case ConsumerName.PSP_PAYMENT_POLL | ConsumerName.PSP_PAYMENT_FETCH:
            return await payment_flow.prepare_poll(conn, fact, now)
        case ConsumerName.PSP_SETTLEMENT_FETCH:
            return await settlement_fetch.prepare_fetch(conn, fact, now)


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
        case duty_lookups.AskCall():
            return await duty_lookups.call(http, environment, effect_call)
        case courier_prices.PriceCall():
            return await courier_prices.call(http, environment, effect_call)
        case (
            payment_flow.CreateCall()
            | payment_flow.FindCall()
            | payment_flow.CaptureCall()
            | payment_flow.CancelCall()
            | payment_flow.PollCall()
        ):
            return await payment_flow.call(http, environment, effect_call)
        case (
            settlement_fetch.ListCall()
            | settlement_fetch.FileCall()
            | settlement_fetch.PayoutCall()
            | settlement_fetch.LinesCall()
        ):
            return await settlement_fetch.call_fetch(http, environment, effect_call)


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
        case duty_lookups.AskAnswer():
            return await duty_lookups.complete(conn, fact, answer, now)
        case courier_prices.PriceAnswer():
            return await courier_prices.complete(conn, fact, answer, now)
        case (
            payment_flow.CreateAnswer()
            | payment_flow.FindAnswer()
            | payment_flow.CaptureAnswer()
            | payment_flow.CancelAnswer()
            | payment_flow.PollAnswer()
        ):
            return await payment_flow.complete(conn, fact, answer, now)
        case (
            settlement_fetch.ListAnswer()
            | settlement_fetch.FileAnswer()
            | settlement_fetch.PayoutAnswer()
            | settlement_fetch.LinesAnswer()
        ):
            return await settlement_fetch.complete_fetch(conn, fact, answer, now)
