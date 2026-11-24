"""The table: each consumer is plain functions, picked by an exhaustive
`match` on its name. The kind is in the consumer's type
(`mor.messaging.routing`), so neither kind can be asked for the other's.

Each case narrows the fact to what the consumer needs (`expect`, or a
`match` where a consumer gets several facts), so consumer functions take
ids and typed facts, never `Fact`. An effect consumer is three steps run
by `run_steps` (plan-messaging-rework.md §3.3)."""

from datetime import datetime

import httpx2

import mor.couriers.prices
import mor.duties.lookups
import mor.echo.flow
import mor.echo.reports
import mor.echo.tracking
import mor.payments.flow
import mor.settlement.fetch
from mor.checkout import flow as checkout_flow
from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import inbound as echo_inbound
from mor.echo import tracking as echo_tracking
from mor.invoices import store as invoice_store
from mor.messaging.delivery import (
    Candidate,
    Deferred,
    EffectRun,
    Ran,
    Skipped,
    run_steps,
)
from mor.messaging.facts import (
    DutyRateNeededV1,
    EchoEventsWantedV1,
    EchoJobRequestedV1,
    EchoPollDueV1,
    EchoReportDueV1,
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
    ConsumerOutcome,
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
    run = EffectRun(
        conn=conn, http=http, environment=environment, candidate=candidate, now=now
    )
    match consumer:
        case ConsumerName.DUTY_RATE_ASK:
            lookup_id = expect(fact, DutyRateNeededV1).duty_lookup_id
            return await run_steps(
                run,
                lookup_id,
                prepare=mor.duties.lookups.prepare_ask,
                call=mor.duties.lookups.ask,
                complete=mor.duties.lookups.complete_ask,
            )
        case ConsumerName.SHIPPING_PRICE_ASK:
            lookup_id = expect(fact, ShippingPriceNeededV1).shipping_price_lookup_id
            return await run_steps(
                run,
                lookup_id,
                prepare=mor.couriers.prices.prepare_rate,
                call=mor.couriers.prices.ask_rate,
                complete=mor.couriers.prices.complete_rate,
            )
        case ConsumerName.PSP_PAYMENT_CREATE:
            attempt_id = expect(fact, PaymentAttemptCreatedV1).payment_attempt_id
            return await run_steps(
                run,
                attempt_id,
                prepare=mor.payments.flow.prepare_create,
                call=mor.payments.flow.send_create,
                complete=mor.payments.flow.complete_create,
            )
        case ConsumerName.PSP_PAYMENT_CAPTURE:
            attempt_id = expect(fact, PaymentAuthorisedV1).payment_attempt_id
            return await run_steps(
                run,
                attempt_id,
                prepare=mor.payments.flow.prepare_capture,
                call=mor.payments.flow.send_capture,
                complete=mor.payments.flow.complete_capture,
            )
        case ConsumerName.PSP_PAYMENT_CANCEL:
            attempt_id = expect(fact, PaymentCancelWantedV1).payment_attempt_id
            return await run_steps(
                run,
                attempt_id,
                prepare=mor.payments.flow.prepare_cancel,
                call=mor.payments.flow.send_cancel,
                complete=mor.payments.flow.complete_cancel,
            )
        case ConsumerName.PSP_PAYMENT_POLL:
            attempt_id = expect(fact, PaymentPollDueV1).payment_attempt_id
            return await run_steps(
                run,
                attempt_id,
                prepare=mor.payments.flow.prepare_poll,
                call=mor.payments.flow.fetch_events,
                complete=mor.payments.flow.complete_poll,
            )
        case ConsumerName.PSP_PAYMENT_FETCH:
            attempt_id = expect(fact, PaymentEventsWantedV1).payment_attempt_id
            return await run_steps(
                run,
                attempt_id,
                prepare=mor.payments.flow.prepare_poll,
                call=mor.payments.flow.fetch_events,
                complete=mor.payments.flow.complete_fetch,
            )
        case ConsumerName.PSP_SETTLEMENT_FETCH:
            account_id = expect(fact, PspSettlementDueV1).psp_account_id
            return await run_steps(
                run,
                account_id,
                prepare=mor.settlement.fetch.prepare_fetch,
                call=mor.settlement.fetch.read_settlement,
                complete=mor.settlement.fetch.complete_fetch,
            )
        # Transitional, with the echo flow (docs/plan-messaging.md §10).
        case ConsumerName.ECHO_SEND:
            job_id = expect(fact, EchoJobRequestedV1).echo_job_id
            return await run_steps(
                run,
                job_id,
                prepare=mor.echo.flow.prepare,
                call=mor.echo.flow.call,
                complete=mor.echo.flow.complete,
            )
        case ConsumerName.ECHO_POLL:
            match fact:
                case EchoPollDueV1() | EchoEventsWantedV1():
                    wanted = fact
                case _:
                    raise WrongFact(f"echo_poll got {fact!r}")
            return await run_steps(
                run,
                wanted,
                prepare=mor.echo.tracking.prepare_poll,
                call=mor.echo.tracking.call_poll,
                complete=mor.echo.tracking.complete_poll,
            )
        case ConsumerName.ECHO_REPORT_FETCH:
            account_id = expect(fact, EchoReportDueV1).account_id
            return await run_steps(
                run,
                account_id,
                prepare=mor.echo.reports.prepare_reports,
                call=mor.echo.reports.call_reports,
                complete=mor.echo.reports.complete_reports,
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
