from enum import StrEnum
from typing import Literal, assert_never

from mor.messaging.facts import MessageType


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


type ConsumerKind = Literal["local", "effect"]


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
        ):
            return ()
        case _:
            assert_never(message_type)


def consumer_kind(consumer: ConsumerName) -> ConsumerKind:
    """`local`: one transaction, no outside call. `effect`: prepare, call
    the outside, complete, each step its own commit (plan-architecture §5)."""
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
        ):
            return "local"
        case (
            ConsumerName.ECHO_SEND
            | ConsumerName.ECHO_POLL
            | ConsumerName.ECHO_REPORT_FETCH
            | ConsumerName.DUTY_RATE_ASK
            | ConsumerName.SHIPPING_PRICE_ASK
        ):
            return "effect"
        case _:
            assert_never(consumer)
