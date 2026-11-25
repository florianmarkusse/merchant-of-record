from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from mor.messaging.facts import MessageType


class WrongFact(Exception):
    """A consumer got a fact routing never sends it: a bug in routing."""


class ConsumerName(StrEnum):
    """Matches the `consumer_name` enum type."""

    PROBE_ECHO = "probe_echo"
    PROBE_FINISH = "probe_finish"
    # Transitional, with the echo flow.
    ECHO_SEND = "echo_send"
    ECHO_INBOX_DECODE = "echo_inbox_decode"
    ECHO_EVENT_APPLY = "echo_event_apply"
    ECHO_TRACKING_START = "echo_tracking_start"
    ECHO_POLL = "echo_poll"
    ECHO_REPORT_FETCH = "echo_report_fetch"
    DUTY_RATE_ASK = "duty_rate_ask"
    SHIPPING_PRICE_ASK = "shipping_price_ask"
    QUOTE_WAIT_NOTIFY = "quote_wait_notify"
    QUOTE_BUILD = "quote_build"
    CHECKOUT_SESSION_EXPIRE = "checkout_session_expire"
    PSP_PAYMENT_CREATE = "psp_payment_create"
    PSP_PAYMENT_CAPTURE = "psp_payment_capture"
    PSP_PAYMENT_CANCEL = "psp_payment_cancel"
    PSP_PAYMENT_POLL = "psp_payment_poll"
    PSP_PAYMENT_FETCH = "psp_payment_fetch"
    PSP_INBOX_DECODE = "psp_inbox_decode"
    PAYMENT_EVENT_APPLY = "payment_event_apply"
    ORDER_CREATE = "order_create"
    INVOICE_ISSUE = "invoice_issue"
    SESSION_PAYMENT_ENDED = "session_payment_ended"
    PSP_SETTLEMENT_FETCH = "psp_settlement_fetch"
    SETTLEMENT_LINE_BOOK = "settlement_line_book"
    SETTLEMENT_PAYOUT_BOOK = "settlement_payout_book"
    SETTLEMENT_PAYOUT_RECONCILE = "settlement_payout_reconcile"
    BANK_STATEMENT_FETCH = "bank_statement_fetch"
    BANK_INBOX_DECODE = "bank_inbox_decode"
    BANK_ENTRY_MATCH = "bank_entry_match"


# The two kinds, as types: the worker hands `handle` only a local consumer
# and `prepare` only an effect one (plan-architecture §5). A new consumer
# joins one of them and `consumer_kind`.
type LocalConsumer = Literal[
    ConsumerName.PROBE_ECHO,
    ConsumerName.PROBE_FINISH,
    ConsumerName.ECHO_INBOX_DECODE,
    ConsumerName.ECHO_EVENT_APPLY,
    ConsumerName.ECHO_TRACKING_START,
    ConsumerName.QUOTE_WAIT_NOTIFY,
    ConsumerName.QUOTE_BUILD,
    ConsumerName.CHECKOUT_SESSION_EXPIRE,
    ConsumerName.PSP_INBOX_DECODE,
    ConsumerName.PAYMENT_EVENT_APPLY,
    ConsumerName.ORDER_CREATE,
    ConsumerName.INVOICE_ISSUE,
    ConsumerName.SESSION_PAYMENT_ENDED,
    ConsumerName.SETTLEMENT_LINE_BOOK,
    ConsumerName.SETTLEMENT_PAYOUT_BOOK,
    ConsumerName.SETTLEMENT_PAYOUT_RECONCILE,
    ConsumerName.BANK_INBOX_DECODE,
    ConsumerName.BANK_ENTRY_MATCH,
]
type EffectConsumer = Literal[
    ConsumerName.ECHO_SEND,
    ConsumerName.ECHO_POLL,
    ConsumerName.ECHO_REPORT_FETCH,
    ConsumerName.DUTY_RATE_ASK,
    ConsumerName.SHIPPING_PRICE_ASK,
    ConsumerName.PSP_PAYMENT_CREATE,
    ConsumerName.PSP_PAYMENT_CAPTURE,
    ConsumerName.PSP_PAYMENT_CANCEL,
    ConsumerName.PSP_PAYMENT_POLL,
    ConsumerName.PSP_PAYMENT_FETCH,
    ConsumerName.PSP_SETTLEMENT_FETCH,
    ConsumerName.BANK_STATEMENT_FETCH,
]


@dataclass(frozen=True)
class Local:
    """One transaction, no outside call."""

    consumer: LocalConsumer


@dataclass(frozen=True)
class Effect:
    """Prepare, call the outside, complete: each step its own commit."""

    consumer: EffectConsumer


def consumers_for(message_type: MessageType) -> tuple[ConsumerName, ...]:
    """The one table of which consumers get which fact. A delivery row is
    written for each when the fact is appended. A fact with no consumer is
    kept as a record of what happened and delivered to nobody."""
    match message_type:
        case MessageType.PROBE_STARTED:
            return (ConsumerName.PROBE_ECHO,)
        case MessageType.PROBE_ECHOED:
            return (ConsumerName.PROBE_FINISH,)
        case MessageType.ECHO_JOB_REQUESTED:
            return (ConsumerName.ECHO_SEND,)
        case MessageType.ECHO_INBOX_STORED:
            return (ConsumerName.ECHO_INBOX_DECODE,)
        case MessageType.ECHO_VENDOR_EVENT_RECEIVED:
            return (ConsumerName.ECHO_EVENT_APPLY,)
        case MessageType.ECHO_JOB_ECHOED:
            return (ConsumerName.ECHO_TRACKING_START,)
        case MessageType.ECHO_POLL_DUE | MessageType.ECHO_EVENTS_WANTED:
            return (ConsumerName.ECHO_POLL,)
        case MessageType.ECHO_REPORT_DUE:
            return (ConsumerName.ECHO_REPORT_FETCH,)
        case MessageType.DUTY_RATE_NEEDED:
            return (ConsumerName.DUTY_RATE_ASK,)
        case MessageType.SHIPPING_PRICE_NEEDED:
            return (ConsumerName.SHIPPING_PRICE_ASK,)
        # A finished lookup tells the sessions waiting on it (4d-2).
        case (
            MessageType.DUTY_RATE_FOUND
            | MessageType.DUTY_RATE_UNKNOWN_CODE
            | MessageType.DUTY_RATE_NEEDS_PERSON
            | MessageType.SHIPPING_PRICE_FOUND
            | MessageType.SHIPPING_PRICE_NOT_SERVED
            | MessageType.SHIPPING_PRICE_NEEDS_PERSON
        ):
            return (ConsumerName.QUOTE_WAIT_NOTIFY,)
        case MessageType.QUOTE_INPUTS_ARRIVED:
            return (ConsumerName.QUOTE_BUILD,)
        case MessageType.CHECKOUT_SESSION_EXPIRY_DUE:
            return (ConsumerName.CHECKOUT_SESSION_EXPIRE,)
        # Payments (docs/plan-payments.md §4).
        case MessageType.PAYMENT_ATTEMPT_CREATED:
            return (ConsumerName.PSP_PAYMENT_CREATE,)
        case MessageType.PAYMENT_AUTHORISED:
            return (ConsumerName.PSP_PAYMENT_CAPTURE,)
        case MessageType.PAYMENT_CANCEL_WANTED:
            return (ConsumerName.PSP_PAYMENT_CANCEL,)
        case MessageType.PAYMENT_POLL_DUE:
            return (ConsumerName.PSP_PAYMENT_POLL,)
        # Its own consumer from when deliveries ran in order and a poll
        # scheduled for later held it back. Folding it into
        # psp_payment_poll would retire a `consumer_name` value for no
        # gain, so it stays.
        case MessageType.PAYMENT_EVENTS_WANTED:
            return (ConsumerName.PSP_PAYMENT_FETCH,)
        case MessageType.PSP_INBOX_STORED:
            return (ConsumerName.PSP_INBOX_DECODE,)
        case MessageType.PSP_EVENT_RECEIVED:
            return (ConsumerName.PAYMENT_EVENT_APPLY,)
        case MessageType.PAYMENT_CAPTURED:
            return (ConsumerName.ORDER_CREATE,)
        # A settled line waiting for its order is booked once the order
        # exists, or becomes an exception once its payment ended otherwise
        # (docs/plan-payments.md §7).
        case MessageType.ORDER_PAID:
            return (ConsumerName.INVOICE_ISSUE, ConsumerName.SETTLEMENT_LINE_BOOK)
        case (
            MessageType.PAYMENT_DECLINED
            | MessageType.PAYMENT_EXPIRED
            | MessageType.PAYMENT_CANCELLED
        ):
            return (
                ConsumerName.SESSION_PAYMENT_ENDED,
                ConsumerName.SETTLEMENT_LINE_BOOK,
            )
        case MessageType.PAYMENT_NEEDS_PERSON:
            return (ConsumerName.SETTLEMENT_LINE_BOOK,)
        case MessageType.PSP_SETTLEMENT_DUE:
            return (ConsumerName.PSP_SETTLEMENT_FETCH,)
        # A line and its payout arrive in any order; each runs the payout's
        # reconciliation (docs/plan-payments.md §7).
        case MessageType.SETTLEMENT_LINE_RECEIVED:
            return (
                ConsumerName.SETTLEMENT_LINE_BOOK,
                ConsumerName.SETTLEMENT_PAYOUT_RECONCILE,
            )
        # A payout may be reported after its credit is read: it runs the
        # bank match too (docs/plan-payments.md §7, the bank statement).
        case MessageType.SETTLEMENT_PAYOUT_REPORTED:
            return (
                ConsumerName.SETTLEMENT_PAYOUT_BOOK,
                ConsumerName.SETTLEMENT_PAYOUT_RECONCILE,
                ConsumerName.BANK_ENTRY_MATCH,
            )
        case MessageType.BANK_STATEMENT_DUE:
            return (ConsumerName.BANK_STATEMENT_FETCH,)
        case MessageType.BANK_INBOX_STORED:
            return (ConsumerName.BANK_INBOX_DECODE,)
        case MessageType.BANK_ENTRY_RECEIVED:
            return (ConsumerName.BANK_ENTRY_MATCH,)
        case (
            MessageType.ECHO_JOB_REFUSED
            | MessageType.ECHO_JOB_NEEDS_PERSON
            | MessageType.ECHO_JOB_CONFIRMED
            | MessageType.ECHO_JOB_ARCHIVED
            | MessageType.DUTY_RATE_DISAGREED
            # Records for now; payments (slice 5) and merchant webhooks
            # consume them.
            | MessageType.CHECKOUT_SESSION_CREATED
            | MessageType.QUOTE_REQUESTED
            | MessageType.QUOTE_MADE
            | MessageType.QUOTE_REFUSED
            | MessageType.CHECKOUT_SESSION_EXPIRED
            # Records for now: what the page shows. A person reads the
            # needs-person list; merchant webhooks and emails consume the
            # rest.
            | MessageType.PAYMENT_ACTION_REQUIRED
            | MessageType.CHECKOUT_SESSION_COMPLETED
            | MessageType.INVOICE_ISSUED
        ):
            return ()


def consumer_kind(consumer: ConsumerName) -> Local | Effect:
    """The consumer, narrowed to its kind."""
    match consumer:
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
            | ConsumerName.SETTLEMENT_LINE_BOOK
            | ConsumerName.SETTLEMENT_PAYOUT_BOOK
            | ConsumerName.SETTLEMENT_PAYOUT_RECONCILE
            | ConsumerName.BANK_INBOX_DECODE
            | ConsumerName.BANK_ENTRY_MATCH
        ):
            return Local(consumer)
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
            | ConsumerName.PSP_SETTLEMENT_FETCH
            | ConsumerName.BANK_STATEMENT_FETCH
        ):
            return Effect(consumer)
