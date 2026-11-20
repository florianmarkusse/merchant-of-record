"""The dispatch tables: each consumer is plain functions, picked by an
exhaustive `match` on its name. A local consumer has `handle`; an effect
consumer has `prepare`, `call` and `complete` (plan-architecture.md §5).
Asking a consumer for a step of the other kind is a bug, so it raises."""

from datetime import datetime
from typing import assert_never

import httpx2

from mor.checkout import flow as checkout_flow
from mor.config import Environment
from mor.couriers import port as courier_port
from mor.couriers import prices as courier_prices
from mor.db.connect import Connection
from mor.duties import lookups as duty_lookups
from mor.duties import port as duty_port
from mor.echo import flow as echo_flow
from mor.echo import inbound as echo_inbound
from mor.echo import reports as echo_reports
from mor.echo import tracking as echo_tracking
from mor.echo.port import OutcomeUnknown, Page, ReportList, Unavailable
from mor.invoices import store as invoice_store
from mor.messaging.facts import Fact
from mor.messaging.outcome import ConsumerOutcome, NoCall, StepResult
from mor.messaging.routing import ConsumerName
from mor.orders import flow as order_flow
from mor.payments import flow as payment_flow
from mor.payments import inbound as payment_inbound
from mor.probe import flow as probe_flow


type EffectCall = (
    echo_flow.EchoCall
    | echo_tracking.FetchEventsCall
    | echo_reports.ReportCall
    | duty_lookups.AskCall
    | courier_prices.PriceCall
    | payment_flow.PaymentCall
)
type EffectResult = (
    echo_flow.EchoResult
    | Page
    | ReportList
    | duty_port.AskResult
    | courier_port.QuoteRateResult
    | payment_flow.PaymentResult
)


class WrongKind(Exception):
    """A step was asked of a consumer of the other kind."""


class WrongCall(Exception):
    """A consumer's `complete` got a call or result it never asks for."""


async def handle(
    conn: Connection,
    consumer: ConsumerName,
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
        case (
            ConsumerName.ECHO_SEND
            | ConsumerName.ECHO_POLL
            | ConsumerName.ECHO_REPORT_FETCH
            | ConsumerName.DUTY_RATE_ASK
            | ConsumerName.SHIPPING_PRICE_ASK
            | ConsumerName.PSP_PAYMENT_CREATE
            | ConsumerName.PSP_PAYMENT_CAPTURE
            | ConsumerName.PSP_PAYMENT_CANCEL
            | ConsumerName.PSP_PAYMENT_POLL
            | ConsumerName.PSP_PAYMENT_FETCH
        ):
            raise WrongKind(f"{consumer} is an effect consumer")
        case _:
            assert_never(consumer)


async def prepare(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
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
        case (
            ConsumerName.PROBE_ECHO
            | ConsumerName.PROBE_FINISH
            | ConsumerName.ECHO_INBOX_DECODE
            | ConsumerName.ECHO_EVENT_APPLY
            | ConsumerName.ECHO_TRACKING_START
            | ConsumerName.QUOTE_WAIT_NOTIFY
            | ConsumerName.QUOTE_BUILD
            | ConsumerName.CHECKOUT_SESSION_EXPIRE
            | ConsumerName.PSP_INBOX_DECODE
            | ConsumerName.PAYMENT_EVENT_APPLY
            | ConsumerName.ORDER_CREATE
            | ConsumerName.INVOICE_ISSUE
            | ConsumerName.SESSION_PAYMENT_ENDED
        ):
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)


async def call(
    http: httpx2.AsyncClient, environment: Environment, effect_call: EffectCall
) -> EffectResult:
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
        case _:
            assert_never(effect_call)


async def complete(
    conn: Connection,
    consumer: ConsumerName,
    fact: Fact,
    effect_call: EffectCall,
    result: EffectResult,
    now: datetime,
) -> StepResult:
    """An effect consumer's last step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            if (
                not isinstance(
                    effect_call,
                    echo_flow.SendCall | echo_flow.FindCall | echo_flow.CancelCall,
                )
                or isinstance(
                    result,
                    Page
                    | ReportList
                    | duty_port.VendorRates
                    | duty_port.UnknownCode
                    | duty_port.Unavailable
                    | duty_port.OutcomeUnknown
                    | duty_port.UndecodableBody
                    | courier_port.CarrierPrice
                    | courier_port.NotServed
                    | courier_port.Unavailable
                    | courier_port.OutcomeUnknown
                    | courier_port.UndecodableBody,
                )
                or payment_flow.is_payment_result(result)
            ):
                raise WrongCall(f"echo_send got {effect_call!r}, {result!r}")
            return await echo_flow.complete(conn, fact, effect_call, result, now)
        case ConsumerName.ECHO_POLL:
            if not isinstance(effect_call, echo_tracking.FetchEventsCall):
                raise WrongCall(f"echo_poll got {effect_call!r}")
            if not isinstance(result, Page | Unavailable | OutcomeUnknown):
                raise WrongCall(f"echo_poll got {result!r}")
            return await echo_tracking.complete_poll(
                conn, fact, effect_call, result, now
            )
        case ConsumerName.ECHO_REPORT_FETCH:
            if not isinstance(
                effect_call, echo_reports.ListReportsCall | echo_reports.FetchReportCall
            ):
                raise WrongCall(f"echo_report_fetch got {effect_call!r}")
            if not isinstance(result, ReportList | Page | Unavailable | OutcomeUnknown):
                raise WrongCall(f"echo_report_fetch got {result!r}")
            return await echo_reports.complete_reports(
                conn, fact, effect_call, result, now
            )
        case ConsumerName.DUTY_RATE_ASK:
            if not isinstance(effect_call, duty_lookups.AskCall):
                raise WrongCall(f"duty_rate_ask got {effect_call!r}")
            if not isinstance(
                result,
                duty_port.VendorRates
                | duty_port.UnknownCode
                | duty_port.Unavailable
                | duty_port.OutcomeUnknown
                | duty_port.UndecodableBody,
            ):
                raise WrongCall(f"duty_rate_ask got {result!r}")
            return await duty_lookups.complete(conn, fact, effect_call, result, now)
        case ConsumerName.SHIPPING_PRICE_ASK:
            if not isinstance(effect_call, courier_prices.PriceCall):
                raise WrongCall(f"shipping_price_ask got {effect_call!r}")
            if not isinstance(
                result,
                courier_port.CarrierPrice
                | courier_port.NotServed
                | courier_port.Unavailable
                | courier_port.OutcomeUnknown
                | courier_port.UndecodableBody,
            ):
                raise WrongCall(f"shipping_price_ask got {result!r}")
            return await courier_prices.complete(conn, fact, effect_call, result, now)
        case (
            ConsumerName.PSP_PAYMENT_CREATE
            | ConsumerName.PSP_PAYMENT_CAPTURE
            | ConsumerName.PSP_PAYMENT_CANCEL
            | ConsumerName.PSP_PAYMENT_POLL
            | ConsumerName.PSP_PAYMENT_FETCH
        ):
            if not payment_flow.is_payment_call(effect_call):
                raise WrongCall(f"{consumer} got {effect_call!r}")
            if not payment_flow.is_payment_result(result):
                raise WrongCall(f"{consumer} got {result!r}")
            match consumer:
                case ConsumerName.PSP_PAYMENT_CREATE:
                    return await payment_flow.complete_create(
                        conn, fact, effect_call, result, now
                    )
                case ConsumerName.PSP_PAYMENT_CAPTURE:
                    return await payment_flow.complete_capture(
                        conn, fact, effect_call, result, now
                    )
                case ConsumerName.PSP_PAYMENT_CANCEL:
                    return await payment_flow.complete_cancel(
                        conn, fact, effect_call, result, now
                    )
                case ConsumerName.PSP_PAYMENT_POLL | ConsumerName.PSP_PAYMENT_FETCH:
                    return await payment_flow.complete_poll(
                        conn, fact, effect_call, result, now
                    )
        case (
            ConsumerName.PROBE_ECHO
            | ConsumerName.PROBE_FINISH
            | ConsumerName.ECHO_INBOX_DECODE
            | ConsumerName.ECHO_EVENT_APPLY
            | ConsumerName.ECHO_TRACKING_START
            | ConsumerName.QUOTE_WAIT_NOTIFY
            | ConsumerName.QUOTE_BUILD
            | ConsumerName.CHECKOUT_SESSION_EXPIRE
            | ConsumerName.PSP_INBOX_DECODE
            | ConsumerName.PAYMENT_EVENT_APPLY
            | ConsumerName.ORDER_CREATE
            | ConsumerName.INVOICE_ISSUE
            | ConsumerName.SESSION_PAYMENT_ENDED
        ):
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)
