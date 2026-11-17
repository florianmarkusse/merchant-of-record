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
        case (
            MessageType.ECHO_JOB_REFUSED
            | MessageType.ECHO_JOB_NEEDS_PERSON
            | MessageType.ECHO_JOB_CONFIRMED
            | MessageType.ECHO_JOB_ARCHIVED
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
        ):
            return "local"
        case (
            ConsumerName.ECHO_SEND
            | ConsumerName.ECHO_POLL
            | ConsumerName.ECHO_REPORT_FETCH
        ):
            return "effect"
        case _:
            assert_never(consumer)
