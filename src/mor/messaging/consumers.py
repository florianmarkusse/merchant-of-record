"""The table: each consumer is plain functions, picked by an exhaustive
`match` on its name. The kind is in the consumer's type
(`mor.messaging.routing`), so neither kind can be asked for the other's.

Each case narrows the fact to what the consumer needs (`expect`, or a
`match` where a consumer gets several facts), so consumer functions take
ids and typed facts, never `Fact`. An effect consumer is three steps run
by `run_steps` (plan-messaging.md §6)."""

from datetime import datetime

import httpx2

import mor.checkout.flow
import mor.couriers.prices
import mor.duties.lookups
import mor.echo.flow
import mor.echo.inbound
import mor.echo.reports
import mor.echo.tracking
import mor.invoices.store
import mor.orders.flow
import mor.payments.flow
import mor.payments.inbound
import mor.probe.flow
import mor.settlement.fetch
import mor.settlement.store
from mor.checkout.sessions import lock_session
from mor.config import Environment
from mor.db.connect import Connection
from mor.messaging.delivery import (
    Candidate,
    Deferred,
    EffectRun,
    Ran,
    Skipped,
    run_steps,
)
from mor.messaging.facts import (
    CheckoutSessionExpiryDueV1,
    DutyRateFoundV1,
    DutyRateNeededV1,
    DutyRateNeedsPersonV1,
    DutyRateUnknownCodeV1,
    EchoEventsWantedV1,
    EchoInboxStoredV1,
    EchoJobEchoedV1,
    EchoJobRequestedV1,
    EchoPollDueV1,
    EchoReportDueV1,
    EchoVendorEventReceivedV1,
    Fact,
    OrderPaidV1,
    PaymentAttemptCreatedV1,
    PaymentAuthorisedV1,
    PaymentCancelledV1,
    PaymentCancelWantedV1,
    PaymentCapturedV1,
    PaymentDeclinedV1,
    PaymentEventsWantedV1,
    PaymentExpiredV1,
    PaymentNeedsPersonV1,
    PaymentPollDueV1,
    ProbeEchoedV1,
    ProbeStartedV1,
    PspEventReceivedV1,
    PspInboxStoredV1,
    PspSettlementDueV1,
    QuoteInputsArrivedV1,
    SettlementLineReceivedV1,
    SettlementPayoutReportedV1,
    ShippingPriceFoundV1,
    ShippingPriceNeededV1,
    ShippingPriceNeedsPersonV1,
    ShippingPriceNotServedV1,
)
from mor.messaging.outcome import (
    ConsumerOutcome,
    Stale,
)
from mor.messaging.routing import (
    ConsumerName,
    EffectConsumer,
    LocalConsumer,
    WrongFact,
)
from mor.orders.store import lock_order
from mor.payments.attempts import lock_attempt
from mor.settlement.store import AlreadyBooked, PendingFor, Unmatched


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
        # Transitional, with the echo flow (docs/plan-messaging.md §9).
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


async def run_local(
    conn: Connection,
    consumer: LocalConsumer,
    fact: Fact,
    environment: Environment,
    now: datetime,
) -> ConsumerOutcome:
    """A local consumer, in the worker's transaction, under the delivery's
    row lock. Each case locks the row the consumer changes before any of
    its code runs and hands it over (plan-messaging.md §5), or
    says why it locks nothing. Never commits."""
    match consumer:
        case ConsumerName.PROBE_ECHO:
            probe_id = expect(fact, ProbeStartedV1).probe_id
            probe = await mor.probe.flow.lock_probe(conn, probe_id)
            return await mor.probe.flow.echo(conn, probe, now)
        case ConsumerName.PROBE_FINISH:
            probe_id = expect(fact, ProbeEchoedV1).probe_id
            probe = await mor.probe.flow.lock_probe(conn, probe_id)
            return await mor.probe.flow.finish(conn, probe, now)
        case ConsumerName.ECHO_INBOX_DECODE:
            # Locks nothing: reads its inbox row, which never changes; each
            # event's fact goes to a consumer that locks its job.
            stored_echo = expect(fact, EchoInboxStoredV1)
            return await mor.echo.inbound.decode_inbox(conn, stored_echo, now)
        case ConsumerName.ECHO_EVENT_APPLY:
            echo_event = expect(fact, EchoVendorEventReceivedV1)
            status = await mor.echo.inbound.lock_job_status(
                conn, echo_event.echo_job_id
            )
            return await mor.echo.inbound.apply_event(conn, status, echo_event, now)
        case ConsumerName.ECHO_TRACKING_START:
            job_id = expect(fact, EchoJobEchoedV1).echo_job_id
            job = await mor.echo.tracking.lock_tracked_job(conn, job_id)
            return await mor.echo.tracking.start_tracking(conn, job, now)
        case ConsumerName.QUOTE_WAIT_NOTIFY:
            # Locks nothing: it only appends a fact per waiting session,
            # which that session's own consumer decides under its lock.
            match fact:
                case (
                    DutyRateFoundV1()
                    | DutyRateUnknownCodeV1()
                    | DutyRateNeedsPersonV1()
                    | ShippingPriceFoundV1()
                    | ShippingPriceNotServedV1()
                    | ShippingPriceNeedsPersonV1()
                ):
                    return await mor.checkout.flow.notify_waiting(conn, fact, now)
                case _:
                    raise WrongFact(f"quote_wait_notify got {fact!r}")
        case ConsumerName.QUOTE_BUILD:
            arrived = expect(fact, QuoteInputsArrivedV1)
            session = await lock_session(conn, arrived.checkout_session_id)
            return await mor.checkout.flow.build_waiting(
                conn, session, arrived, environment, now
            )
        case ConsumerName.CHECKOUT_SESSION_EXPIRE:
            due = expect(fact, CheckoutSessionExpiryDueV1)
            session = await lock_session(conn, due.checkout_session_id)
            return await mor.checkout.flow.expire_session(conn, session, due, now)
        case ConsumerName.PSP_INBOX_DECODE:
            # Locks nothing: reads its inbox row, which never changes; each
            # event's fact goes to a consumer that locks its attempt.
            stored_psp = expect(fact, PspInboxStoredV1)
            return await mor.payments.inbound.decode_inbox(conn, stored_psp, now)
        case ConsumerName.PAYMENT_EVENT_APPLY:
            psp_event = expect(fact, PspEventReceivedV1)
            attempt = await lock_attempt(conn, psp_event.payment_attempt_id)
            return await mor.payments.inbound.apply_event(conn, attempt, psp_event, now)
        case ConsumerName.ORDER_CREATE:
            captured = expect(fact, PaymentCapturedV1)
            session = await lock_session(conn, captured.checkout_session_id)
            assert session is not None, f"no session for {captured!r}"
            return await mor.orders.flow.create_order(conn, session, captured, now)
        case ConsumerName.INVOICE_ISSUE:
            order_id = expect(fact, OrderPaidV1).order_id
            paid_order = await lock_order(conn, order_id)
            assert paid_order is not None, f"no order {order_id}"
            return await mor.invoices.store.issue_invoice(conn, paid_order, now)
        case ConsumerName.SESSION_PAYMENT_ENDED:
            match fact:
                case PaymentDeclinedV1() | PaymentExpiredV1() | PaymentCancelledV1():
                    session = await lock_session(conn, fact.checkout_session_id)
                    return await mor.checkout.flow.end_payment(
                        conn, session, fact.payment_attempt_id, now
                    )
                case _:
                    raise WrongFact(f"session_payment_ended got {fact!r}")
        case ConsumerName.SETTLEMENT_LINE_BOOK:
            # A line with its attempt, the lines waiting for an order, or
            # the lines of a payment that ended: under the attempt's lock.
            # A line that names no attempt: under its own.
            match fact:
                case SettlementLineReceivedV1(settlement_line_id=line_id):
                    booking = await mor.settlement.store.line_booking(conn, line_id)
                    match booking:
                        case AlreadyBooked():
                            return Stale()
                        case Unmatched():
                            line = await mor.settlement.store.lock_line(conn, line_id)
                            return await mor.settlement.store.book_unmatched_line(
                                conn, line, now
                            )
                        case PendingFor(attempt_id=attempt_id):
                            pass
                case OrderPaidV1(order_id=order_id):
                    attempt_id = await mor.settlement.store.order_attempt(
                        conn, order_id
                    )
                case (
                    PaymentDeclinedV1(payment_attempt_id=attempt_id)
                    | PaymentExpiredV1(payment_attempt_id=attempt_id)
                    | PaymentCancelledV1(payment_attempt_id=attempt_id)
                    | PaymentNeedsPersonV1(payment_attempt_id=attempt_id)
                ):
                    pass
                case _:
                    raise WrongFact(f"settlement_line_book got {fact!r}")
            attempt = await lock_attempt(conn, attempt_id)
            return await mor.settlement.store.settle_pending(conn, attempt, now)
        case ConsumerName.SETTLEMENT_PAYOUT_BOOK:
            payout_id = expect(fact, SettlementPayoutReportedV1).settlement_payout_id
            payout = await mor.settlement.store.lock_payout(conn, payout_id)
            return await mor.settlement.store.book_payout(conn, payout, now)
        case ConsumerName.SETTLEMENT_PAYOUT_RECONCILE:
            # A line names its payout by the PSP's id, which never changes;
            # the payout may not be stored yet.
            match fact:
                case SettlementLineReceivedV1() | SettlementPayoutReportedV1():
                    key = await mor.settlement.store.payout_key(conn, fact)
                case _:
                    raise WrongFact(f"settlement_payout_reconcile got {fact!r}")
            payout = await mor.settlement.store.lock_payout_by_key(conn, key)
            return await mor.settlement.store.reconcile_payout(conn, payout, now)
