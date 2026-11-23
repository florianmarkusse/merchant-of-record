"""Facts: immutable records of what happened, one per type and version.

A shape change is a new version (`ProbeStartedV2`), even an additive one,
and the old one stays decodable while prod holds undelivered messages of it.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, assert_never

import msgspec


class AggregateType(StrEnum):
    """Matches the `aggregate_type` enum type."""

    PROBE = "probe"
    ECHO_JOB = "echo_job"  # transitional, with the echo flow
    ECHO_ACCOUNT = "echo_account"  # transitional, with the echo flow
    DUTY_LOOKUP = "duty_lookup"
    SHIPPING_PRICE_LOOKUP = "shipping_price_lookup"
    CHECKOUT_SESSION = "checkout_session"
    PAYMENT_ATTEMPT = "payment_attempt"
    PSP_ACCOUNT = "psp_account"
    ORDER = "order"
    SETTLEMENT_LINE = "settlement_line"
    SETTLEMENT_BATCH = "settlement_batch"


class MessageType(StrEnum):
    """Matches the `message_type` enum type. Past tense."""

    PROBE_STARTED = "probe_started"
    PROBE_ECHOED = "probe_echoed"
    # Transitional, with the echo flow (docs/plan-messaging.md §10).
    ECHO_JOB_REQUESTED = "echo_job_requested"
    ECHO_JOB_ECHOED = "echo_job_echoed"
    ECHO_JOB_REFUSED = "echo_job_refused"
    ECHO_JOB_NEEDS_PERSON = "echo_job_needs_person"
    ECHO_INBOX_STORED = "echo_inbox_stored"
    ECHO_VENDOR_EVENT_RECEIVED = "echo_vendor_event_received"
    ECHO_JOB_CONFIRMED = "echo_job_confirmed"
    ECHO_JOB_ARCHIVED = "echo_job_archived"
    ECHO_POLL_DUE = "echo_poll_due"
    ECHO_EVENTS_WANTED = "echo_events_wanted"
    ECHO_REPORT_DUE = "echo_report_due"
    DUTY_RATE_NEEDED = "duty_rate_needed"
    DUTY_RATE_FOUND = "duty_rate_found"
    DUTY_RATE_UNKNOWN_CODE = "duty_rate_unknown_code"
    DUTY_RATE_NEEDS_PERSON = "duty_rate_needs_person"
    DUTY_RATE_DISAGREED = "duty_rate_disagreed"
    SHIPPING_PRICE_NEEDED = "shipping_price_needed"
    SHIPPING_PRICE_FOUND = "shipping_price_found"
    SHIPPING_PRICE_NOT_SERVED = "shipping_price_not_served"
    SHIPPING_PRICE_NEEDS_PERSON = "shipping_price_needs_person"
    CHECKOUT_SESSION_CREATED = "checkout_session_created"
    QUOTE_REQUESTED = "quote_requested"
    QUOTE_MADE = "quote_made"
    QUOTE_REFUSED = "quote_refused"
    QUOTE_INPUTS_ARRIVED = "quote_inputs_arrived"
    CHECKOUT_SESSION_EXPIRY_DUE = "checkout_session_expiry_due"
    CHECKOUT_SESSION_EXPIRED = "checkout_session_expired"
    PAYMENT_ATTEMPT_CREATED = "payment_attempt_created"
    PAYMENT_ACTION_REQUIRED = "payment_action_required"
    PAYMENT_AUTHORISED = "payment_authorised"
    PAYMENT_CAPTURED = "payment_captured"
    PAYMENT_DECLINED = "payment_declined"
    PAYMENT_EXPIRED = "payment_expired"
    PAYMENT_CANCELLED = "payment_cancelled"
    PAYMENT_NEEDS_PERSON = "payment_needs_person"
    PAYMENT_CANCEL_WANTED = "payment_cancel_wanted"
    PAYMENT_POLL_DUE = "payment_poll_due"
    PAYMENT_EVENTS_WANTED = "payment_events_wanted"
    PSP_INBOX_STORED = "psp_inbox_stored"
    PSP_EVENT_RECEIVED = "psp_event_received"
    CHECKOUT_SESSION_COMPLETED = "checkout_session_completed"
    ORDER_PAID = "order_paid"
    INVOICE_ISSUED = "invoice_issued"
    PSP_SETTLEMENT_DUE = "psp_settlement_due"
    SETTLEMENT_LINE_RECEIVED = "settlement_line_received"
    SETTLEMENT_BATCH_RECEIVED = "settlement_batch_received"


@dataclass(frozen=True)
class Aggregate:
    type: AggregateType
    id: uuid.UUID


class ProbeStartedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


class ProbeEchoedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


class EchoJobRequestedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoJobEchoedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID
    vendor_echo_id: str


class EchoJobRefusedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID
    reason: str


class EchoJobNeedsPersonV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A send whose answer was lost, on a vendor that promises nothing about
    a resend, and the vendor does not know it: a person decides."""

    echo_job_id: uuid.UUID


class EchoInboxStoredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Raw bytes from the vendor (a webhook, a page, a file) were stored,
    not yet decoded."""

    account_id: uuid.UUID
    inbox_id: uuid.UUID


class EchoVendorEventReceivedV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    """One event about a job: decoded from the vendor's bytes (`inbox_id`
    set), or recorded by hand (`manual`, the person is the fact's actor)."""

    echo_job_id: uuid.UUID
    vendor_event_id: str
    kind: Literal["confirmed", "archived"]
    occurred_at: datetime
    source: Literal["vendor", "manual"]
    inbox_id: uuid.UUID | None


class EchoJobConfirmedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoJobArchivedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoPollDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: the time to poll a job's events came. Whether a poll is
    still wanted is decided under the job's lock."""

    echo_job_id: uuid.UUID


class EchoEventsWantedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A thin webhook said the job has news, without saying what."""

    echo_job_id: uuid.UUID


class EchoReportDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: the time to look for new report files of an account."""

    account_id: uuid.UUID


class DutyRateNeededV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A combination (vendor, destination, origin, HS code) was seen for
    the first time: ask the vendor once."""

    duty_lookup_id: uuid.UUID


class DutyRateFoundV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    duty_lookup_id: uuid.UUID


class DutyRateUnknownCodeV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor does not know the HS code."""

    duty_lookup_id: uuid.UUID


class DutyRateNeedsPersonV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor's answer could not be read."""

    duty_lookup_id: uuid.UUID
    detail: str


class DutyRateDisagreedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor's rate for a charge differs from our own row's. Rates are
    exact fractions written as "numerator/denominator"."""

    duty_lookup_id: uuid.UUID
    charge: str
    vendor_rate: str
    own_rate: str
    own_line_no: int


class ShippingPriceNeededV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """No usable price is held for a (carrier, service, lane, parcel) and
    none is being asked: ask the carrier."""

    shipping_price_lookup_id: uuid.UUID


class ShippingPriceFoundV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    shipping_price_lookup_id: uuid.UUID


class ShippingPriceNotServedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The carrier does not carry this service, lane or parcel."""

    shipping_price_lookup_id: uuid.UUID
    detail: str


class ShippingPriceNeedsPersonV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    """The carrier's answer could not be read."""

    shipping_price_lookup_id: uuid.UUID
    detail: str


class CheckoutSessionCreatedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A merchant created a checkout session (4d-1). No consumers yet."""

    checkout_session_id: uuid.UUID
    merchant_id: uuid.UUID


class QuoteRequestedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A buyer gave an address and an input is missing: asked, waiting."""

    checkout_session_id: uuid.UUID
    address_id: uuid.UUID


class QuoteMadeV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    checkout_session_id: uuid.UUID
    quote_id: uuid.UUID


class QuoteRefusedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    checkout_session_id: uuid.UUID
    quote_id: uuid.UUID
    code: str


class QuoteInputsArrivedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A lookup this session's address waited on has finished: build again."""

    checkout_session_id: uuid.UUID
    address_id: uuid.UUID


class CheckoutSessionExpiryDueV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    """Delivered at the session's `expires_at`: expire it unless it is done."""

    checkout_session_id: uuid.UUID


class CheckoutSessionExpiredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    checkout_session_id: uuid.UUID


class PaymentAttemptCreatedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The buyer chose to pay a quote's option: ask the PSP."""

    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID


class PaymentActionRequiredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The PSP made the payment; the buyer must act on its page or a QR."""

    payment_attempt_id: uuid.UUID


class PaymentAuthorisedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Approved and not captured yet: capture it."""

    payment_attempt_id: uuid.UUID


class PaymentCapturedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The money is taken, for exactly the attempt's amount."""

    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID


class PaymentDeclinedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID
    reason: str


class PaymentExpiredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID


class PaymentCancelledV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID


class PaymentNeedsPersonV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """An answer lost on a `none` guard, or the PSP reported something that
    does not fit (another amount, a second capture): a person decides."""

    payment_attempt_id: uuid.UUID
    checkout_session_id: uuid.UUID
    detail: str


class PaymentCancelWantedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The buyer chose to pay another way: tell the PSP."""

    payment_attempt_id: uuid.UUID


class PaymentPollDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: ask the PSP how the payment stands (a poll account's turn,
    or a QR's expiry). What it says decides, under the attempt's lock."""

    payment_attempt_id: uuid.UUID


class PaymentEventsWantedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A thin webhook said the payment has news, without saying what."""

    payment_attempt_id: uuid.UUID


class PspInboxStoredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Raw bytes from the PSP (a webhook or a fetched page) were stored."""

    psp_account_id: uuid.UUID
    inbox_id: uuid.UUID


class PspEventReceivedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One PSP event about an attempt, decoded from stored bytes. The
    amount is ours, converted from the PSP's units."""

    payment_attempt_id: uuid.UUID
    vendor_event_id: str
    provider_payment_id: str
    kind: Literal["authorised", "captured", "declined", "expired", "cancelled"]
    amount_minor: int
    currency: str
    occurred_at: datetime
    inbox_id: uuid.UUID


class CheckoutSessionCompletedV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    checkout_session_id: uuid.UUID
    order_id: uuid.UUID


class OrderPaidV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """An order exists and is paid; its invoice is issued next."""

    order_id: uuid.UUID
    checkout_session_id: uuid.UUID
    merchant_id: uuid.UUID


class InvoiceIssuedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """An order's invoice is issued, numbered in its series. A record for
    now; merchant webhooks and the buyer's email consume it later."""

    invoice_id: uuid.UUID
    order_id: uuid.UUID
    merchant_id: uuid.UUID


class PspSettlementDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: look for settlement the PSP has published, one report or
    page per look (docs/plan-payments.md §7.1)."""

    psp_account_id: uuid.UUID


class SettlementLineReceivedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A settled line, stored once by the PSP's line id: book it."""

    settlement_line_id: uuid.UUID
    psp_account_id: uuid.UUID


class SettlementBatchReceivedV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    """A batch with all its lines stored: book its payout."""

    settlement_batch_id: uuid.UUID
    psp_account_id: uuid.UUID


type Fact = (
    ProbeStartedV1
    | ProbeEchoedV1
    | EchoJobRequestedV1
    | EchoJobEchoedV1
    | EchoJobRefusedV1
    | EchoJobNeedsPersonV1
    | EchoInboxStoredV1
    | EchoVendorEventReceivedV1
    | EchoJobConfirmedV1
    | EchoJobArchivedV1
    | EchoPollDueV1
    | EchoEventsWantedV1
    | EchoReportDueV1
    | DutyRateNeededV1
    | DutyRateFoundV1
    | DutyRateUnknownCodeV1
    | DutyRateNeedsPersonV1
    | DutyRateDisagreedV1
    | ShippingPriceNeededV1
    | ShippingPriceFoundV1
    | ShippingPriceNotServedV1
    | ShippingPriceNeedsPersonV1
    | CheckoutSessionCreatedV1
    | QuoteRequestedV1
    | QuoteMadeV1
    | QuoteRefusedV1
    | QuoteInputsArrivedV1
    | CheckoutSessionExpiryDueV1
    | CheckoutSessionExpiredV1
    | PaymentAttemptCreatedV1
    | PaymentActionRequiredV1
    | PaymentAuthorisedV1
    | PaymentCapturedV1
    | PaymentDeclinedV1
    | PaymentExpiredV1
    | PaymentCancelledV1
    | PaymentNeedsPersonV1
    | PaymentCancelWantedV1
    | PaymentPollDueV1
    | PaymentEventsWantedV1
    | PspInboxStoredV1
    | PspEventReceivedV1
    | CheckoutSessionCompletedV1
    | OrderPaidV1
    | InvoiceIssuedV1
    | PspSettlementDueV1
    | SettlementLineReceivedV1
    | SettlementBatchReceivedV1
)


@dataclass(frozen=True)
class FactType:
    message_type: MessageType
    schema_version: int


@dataclass(frozen=True)
class UndecodableFact:
    message_type: MessageType
    schema_version: int
    reason: Literal["unknown_version", "invalid_payload"]
    detail: str


def fact_type(fact: Fact) -> FactType:
    match fact:
        case ProbeStartedV1():
            return FactType(MessageType.PROBE_STARTED, 1)
        case ProbeEchoedV1():
            return FactType(MessageType.PROBE_ECHOED, 1)
        case EchoJobRequestedV1():
            return FactType(MessageType.ECHO_JOB_REQUESTED, 1)
        case EchoJobEchoedV1():
            return FactType(MessageType.ECHO_JOB_ECHOED, 1)
        case EchoJobRefusedV1():
            return FactType(MessageType.ECHO_JOB_REFUSED, 1)
        case EchoJobNeedsPersonV1():
            return FactType(MessageType.ECHO_JOB_NEEDS_PERSON, 1)
        case EchoInboxStoredV1():
            return FactType(MessageType.ECHO_INBOX_STORED, 1)
        case EchoVendorEventReceivedV1():
            return FactType(MessageType.ECHO_VENDOR_EVENT_RECEIVED, 1)
        case EchoJobConfirmedV1():
            return FactType(MessageType.ECHO_JOB_CONFIRMED, 1)
        case EchoJobArchivedV1():
            return FactType(MessageType.ECHO_JOB_ARCHIVED, 1)
        case EchoPollDueV1():
            return FactType(MessageType.ECHO_POLL_DUE, 1)
        case EchoEventsWantedV1():
            return FactType(MessageType.ECHO_EVENTS_WANTED, 1)
        case EchoReportDueV1():
            return FactType(MessageType.ECHO_REPORT_DUE, 1)
        case DutyRateNeededV1():
            return FactType(MessageType.DUTY_RATE_NEEDED, 1)
        case DutyRateFoundV1():
            return FactType(MessageType.DUTY_RATE_FOUND, 1)
        case DutyRateUnknownCodeV1():
            return FactType(MessageType.DUTY_RATE_UNKNOWN_CODE, 1)
        case DutyRateNeedsPersonV1():
            return FactType(MessageType.DUTY_RATE_NEEDS_PERSON, 1)
        case DutyRateDisagreedV1():
            return FactType(MessageType.DUTY_RATE_DISAGREED, 1)
        case ShippingPriceNeededV1():
            return FactType(MessageType.SHIPPING_PRICE_NEEDED, 1)
        case ShippingPriceFoundV1():
            return FactType(MessageType.SHIPPING_PRICE_FOUND, 1)
        case ShippingPriceNotServedV1():
            return FactType(MessageType.SHIPPING_PRICE_NOT_SERVED, 1)
        case ShippingPriceNeedsPersonV1():
            return FactType(MessageType.SHIPPING_PRICE_NEEDS_PERSON, 1)
        case CheckoutSessionCreatedV1():
            return FactType(MessageType.CHECKOUT_SESSION_CREATED, 1)
        case QuoteRequestedV1():
            return FactType(MessageType.QUOTE_REQUESTED, 1)
        case QuoteMadeV1():
            return FactType(MessageType.QUOTE_MADE, 1)
        case QuoteRefusedV1():
            return FactType(MessageType.QUOTE_REFUSED, 1)
        case QuoteInputsArrivedV1():
            return FactType(MessageType.QUOTE_INPUTS_ARRIVED, 1)
        case CheckoutSessionExpiryDueV1():
            return FactType(MessageType.CHECKOUT_SESSION_EXPIRY_DUE, 1)
        case CheckoutSessionExpiredV1():
            return FactType(MessageType.CHECKOUT_SESSION_EXPIRED, 1)
        case PaymentAttemptCreatedV1():
            return FactType(MessageType.PAYMENT_ATTEMPT_CREATED, 1)
        case PaymentActionRequiredV1():
            return FactType(MessageType.PAYMENT_ACTION_REQUIRED, 1)
        case PaymentAuthorisedV1():
            return FactType(MessageType.PAYMENT_AUTHORISED, 1)
        case PaymentCapturedV1():
            return FactType(MessageType.PAYMENT_CAPTURED, 1)
        case PaymentDeclinedV1():
            return FactType(MessageType.PAYMENT_DECLINED, 1)
        case PaymentExpiredV1():
            return FactType(MessageType.PAYMENT_EXPIRED, 1)
        case PaymentCancelledV1():
            return FactType(MessageType.PAYMENT_CANCELLED, 1)
        case PaymentNeedsPersonV1():
            return FactType(MessageType.PAYMENT_NEEDS_PERSON, 1)
        case PaymentCancelWantedV1():
            return FactType(MessageType.PAYMENT_CANCEL_WANTED, 1)
        case PaymentPollDueV1():
            return FactType(MessageType.PAYMENT_POLL_DUE, 1)
        case PaymentEventsWantedV1():
            return FactType(MessageType.PAYMENT_EVENTS_WANTED, 1)
        case PspInboxStoredV1():
            return FactType(MessageType.PSP_INBOX_STORED, 1)
        case PspEventReceivedV1():
            return FactType(MessageType.PSP_EVENT_RECEIVED, 1)
        case CheckoutSessionCompletedV1():
            return FactType(MessageType.CHECKOUT_SESSION_COMPLETED, 1)
        case OrderPaidV1():
            return FactType(MessageType.ORDER_PAID, 1)
        case InvoiceIssuedV1():
            return FactType(MessageType.INVOICE_ISSUED, 1)
        case PspSettlementDueV1():
            return FactType(MessageType.PSP_SETTLEMENT_DUE, 1)
        case SettlementLineReceivedV1():
            return FactType(MessageType.SETTLEMENT_LINE_RECEIVED, 1)
        case SettlementBatchReceivedV1():
            return FactType(MessageType.SETTLEMENT_BATCH_RECEIVED, 1)
        case _:
            assert_never(fact)


def aggregate_of(fact: Fact) -> Aggregate:
    """The aggregate a fact belongs to; its order is kept per aggregate."""
    match fact:
        case ProbeStartedV1(probe_id=probe_id) | ProbeEchoedV1(probe_id=probe_id):
            return Aggregate(AggregateType.PROBE, probe_id)
        case (
            EchoJobRequestedV1(echo_job_id=job_id)
            | EchoJobEchoedV1(echo_job_id=job_id)
            | EchoJobRefusedV1(echo_job_id=job_id)
            | EchoJobNeedsPersonV1(echo_job_id=job_id)
            | EchoVendorEventReceivedV1(echo_job_id=job_id)
            | EchoJobConfirmedV1(echo_job_id=job_id)
            | EchoJobArchivedV1(echo_job_id=job_id)
            | EchoPollDueV1(echo_job_id=job_id)
            | EchoEventsWantedV1(echo_job_id=job_id)
        ):
            return Aggregate(AggregateType.ECHO_JOB, job_id)
        case (
            EchoInboxStoredV1(account_id=account_id)
            | EchoReportDueV1(account_id=account_id)
        ):
            return Aggregate(AggregateType.ECHO_ACCOUNT, account_id)
        case (
            DutyRateNeededV1(duty_lookup_id=lookup_id)
            | DutyRateFoundV1(duty_lookup_id=lookup_id)
            | DutyRateUnknownCodeV1(duty_lookup_id=lookup_id)
            | DutyRateNeedsPersonV1(duty_lookup_id=lookup_id)
            | DutyRateDisagreedV1(duty_lookup_id=lookup_id)
        ):
            return Aggregate(AggregateType.DUTY_LOOKUP, lookup_id)
        case (
            ShippingPriceNeededV1(shipping_price_lookup_id=lookup_id)
            | ShippingPriceFoundV1(shipping_price_lookup_id=lookup_id)
            | ShippingPriceNotServedV1(shipping_price_lookup_id=lookup_id)
            | ShippingPriceNeedsPersonV1(shipping_price_lookup_id=lookup_id)
        ):
            return Aggregate(AggregateType.SHIPPING_PRICE_LOOKUP, lookup_id)
        case (
            CheckoutSessionCreatedV1(checkout_session_id=session_id)
            | QuoteRequestedV1(checkout_session_id=session_id)
            | QuoteMadeV1(checkout_session_id=session_id)
            | QuoteRefusedV1(checkout_session_id=session_id)
            | QuoteInputsArrivedV1(checkout_session_id=session_id)
            | CheckoutSessionExpiryDueV1(checkout_session_id=session_id)
            | CheckoutSessionExpiredV1(checkout_session_id=session_id)
            | CheckoutSessionCompletedV1(checkout_session_id=session_id)
        ):
            return Aggregate(AggregateType.CHECKOUT_SESSION, session_id)
        case (
            PaymentAttemptCreatedV1(payment_attempt_id=attempt_id)
            | PaymentActionRequiredV1(payment_attempt_id=attempt_id)
            | PaymentAuthorisedV1(payment_attempt_id=attempt_id)
            | PaymentCapturedV1(payment_attempt_id=attempt_id)
            | PaymentDeclinedV1(payment_attempt_id=attempt_id)
            | PaymentExpiredV1(payment_attempt_id=attempt_id)
            | PaymentCancelledV1(payment_attempt_id=attempt_id)
            | PaymentNeedsPersonV1(payment_attempt_id=attempt_id)
            | PaymentCancelWantedV1(payment_attempt_id=attempt_id)
            | PaymentPollDueV1(payment_attempt_id=attempt_id)
            | PaymentEventsWantedV1(payment_attempt_id=attempt_id)
            | PspEventReceivedV1(payment_attempt_id=attempt_id)
        ):
            return Aggregate(AggregateType.PAYMENT_ATTEMPT, attempt_id)
        case (
            PspInboxStoredV1(psp_account_id=account_id)
            | PspSettlementDueV1(psp_account_id=account_id)
        ):
            return Aggregate(AggregateType.PSP_ACCOUNT, account_id)
        case SettlementLineReceivedV1(settlement_line_id=line_id):
            return Aggregate(AggregateType.SETTLEMENT_LINE, line_id)
        case SettlementBatchReceivedV1(settlement_batch_id=batch_id):
            return Aggregate(AggregateType.SETTLEMENT_BATCH, batch_id)
        case OrderPaidV1(order_id=order_id) | InvoiceIssuedV1(order_id=order_id):
            return Aggregate(AggregateType.ORDER, order_id)
        case _:
            assert_never(fact)


def encode(fact: Fact) -> bytes:
    return msgspec.json.encode(fact)


def _decode[T](payload: bytes, record: type[T]) -> T | str:
    try:
        return msgspec.json.decode(payload, type=record)
    except msgspec.DecodeError as error:
        return str(error)


def decode(
    message_type: MessageType, schema_version: int, payload: bytes
) -> Fact | UndecodableFact:
    """Bytes from the outbox back into the record they were written from.
    Every type has only version 1 so far."""
    if schema_version != 1:
        return UndecodableFact(
            message_type=message_type,
            schema_version=schema_version,
            reason="unknown_version",
            detail="",
        )
    decoded: Fact | str
    match message_type:
        case MessageType.PROBE_STARTED:
            decoded = _decode(payload, ProbeStartedV1)
        case MessageType.PROBE_ECHOED:
            decoded = _decode(payload, ProbeEchoedV1)
        case MessageType.ECHO_JOB_REQUESTED:
            decoded = _decode(payload, EchoJobRequestedV1)
        case MessageType.ECHO_JOB_ECHOED:
            decoded = _decode(payload, EchoJobEchoedV1)
        case MessageType.ECHO_JOB_REFUSED:
            decoded = _decode(payload, EchoJobRefusedV1)
        case MessageType.ECHO_JOB_NEEDS_PERSON:
            decoded = _decode(payload, EchoJobNeedsPersonV1)
        case MessageType.ECHO_INBOX_STORED:
            decoded = _decode(payload, EchoInboxStoredV1)
        case MessageType.ECHO_VENDOR_EVENT_RECEIVED:
            decoded = _decode(payload, EchoVendorEventReceivedV1)
        case MessageType.ECHO_JOB_CONFIRMED:
            decoded = _decode(payload, EchoJobConfirmedV1)
        case MessageType.ECHO_JOB_ARCHIVED:
            decoded = _decode(payload, EchoJobArchivedV1)
        case MessageType.ECHO_POLL_DUE:
            decoded = _decode(payload, EchoPollDueV1)
        case MessageType.ECHO_EVENTS_WANTED:
            decoded = _decode(payload, EchoEventsWantedV1)
        case MessageType.ECHO_REPORT_DUE:
            decoded = _decode(payload, EchoReportDueV1)
        case MessageType.DUTY_RATE_NEEDED:
            decoded = _decode(payload, DutyRateNeededV1)
        case MessageType.DUTY_RATE_FOUND:
            decoded = _decode(payload, DutyRateFoundV1)
        case MessageType.DUTY_RATE_UNKNOWN_CODE:
            decoded = _decode(payload, DutyRateUnknownCodeV1)
        case MessageType.DUTY_RATE_NEEDS_PERSON:
            decoded = _decode(payload, DutyRateNeedsPersonV1)
        case MessageType.DUTY_RATE_DISAGREED:
            decoded = _decode(payload, DutyRateDisagreedV1)
        case MessageType.SHIPPING_PRICE_NEEDED:
            decoded = _decode(payload, ShippingPriceNeededV1)
        case MessageType.SHIPPING_PRICE_FOUND:
            decoded = _decode(payload, ShippingPriceFoundV1)
        case MessageType.SHIPPING_PRICE_NOT_SERVED:
            decoded = _decode(payload, ShippingPriceNotServedV1)
        case MessageType.SHIPPING_PRICE_NEEDS_PERSON:
            decoded = _decode(payload, ShippingPriceNeedsPersonV1)
        case MessageType.CHECKOUT_SESSION_CREATED:
            decoded = _decode(payload, CheckoutSessionCreatedV1)
        case MessageType.QUOTE_REQUESTED:
            decoded = _decode(payload, QuoteRequestedV1)
        case MessageType.QUOTE_MADE:
            decoded = _decode(payload, QuoteMadeV1)
        case MessageType.QUOTE_REFUSED:
            decoded = _decode(payload, QuoteRefusedV1)
        case MessageType.QUOTE_INPUTS_ARRIVED:
            decoded = _decode(payload, QuoteInputsArrivedV1)
        case MessageType.CHECKOUT_SESSION_EXPIRY_DUE:
            decoded = _decode(payload, CheckoutSessionExpiryDueV1)
        case MessageType.CHECKOUT_SESSION_EXPIRED:
            decoded = _decode(payload, CheckoutSessionExpiredV1)
        case MessageType.PAYMENT_ATTEMPT_CREATED:
            decoded = _decode(payload, PaymentAttemptCreatedV1)
        case MessageType.PAYMENT_ACTION_REQUIRED:
            decoded = _decode(payload, PaymentActionRequiredV1)
        case MessageType.PAYMENT_AUTHORISED:
            decoded = _decode(payload, PaymentAuthorisedV1)
        case MessageType.PAYMENT_CAPTURED:
            decoded = _decode(payload, PaymentCapturedV1)
        case MessageType.PAYMENT_DECLINED:
            decoded = _decode(payload, PaymentDeclinedV1)
        case MessageType.PAYMENT_EXPIRED:
            decoded = _decode(payload, PaymentExpiredV1)
        case MessageType.PAYMENT_CANCELLED:
            decoded = _decode(payload, PaymentCancelledV1)
        case MessageType.PAYMENT_NEEDS_PERSON:
            decoded = _decode(payload, PaymentNeedsPersonV1)
        case MessageType.PAYMENT_CANCEL_WANTED:
            decoded = _decode(payload, PaymentCancelWantedV1)
        case MessageType.PAYMENT_POLL_DUE:
            decoded = _decode(payload, PaymentPollDueV1)
        case MessageType.PAYMENT_EVENTS_WANTED:
            decoded = _decode(payload, PaymentEventsWantedV1)
        case MessageType.PSP_INBOX_STORED:
            decoded = _decode(payload, PspInboxStoredV1)
        case MessageType.PSP_EVENT_RECEIVED:
            decoded = _decode(payload, PspEventReceivedV1)
        case MessageType.CHECKOUT_SESSION_COMPLETED:
            decoded = _decode(payload, CheckoutSessionCompletedV1)
        case MessageType.ORDER_PAID:
            decoded = _decode(payload, OrderPaidV1)
        case MessageType.INVOICE_ISSUED:
            decoded = _decode(payload, InvoiceIssuedV1)
        case MessageType.PSP_SETTLEMENT_DUE:
            decoded = _decode(payload, PspSettlementDueV1)
        case MessageType.SETTLEMENT_LINE_RECEIVED:
            decoded = _decode(payload, SettlementLineReceivedV1)
        case MessageType.SETTLEMENT_BATCH_RECEIVED:
            decoded = _decode(payload, SettlementBatchReceivedV1)
        case _:
            assert_never(message_type)
    if isinstance(decoded, str):
        return UndecodableFact(
            message_type=message_type,
            schema_version=schema_version,
            reason="invalid_payload",
            detail=decoded,
        )
    return decoded
