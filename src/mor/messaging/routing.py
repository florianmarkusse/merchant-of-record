from enum import StrEnum
from typing import Literal, assert_never

from mor.messaging.facts import MessageType


class ConsumerName(StrEnum):
    """Matches the `consumer_name` enum type."""

    PROBE_ECHO = "probe_echo"
    PROBE_FINISH = "probe_finish"
    ECHO_SEND = "echo_send"  # transitional, with the echo flow


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
        case (
            MessageType.ECHO_JOB_ECHOED
            | MessageType.ECHO_JOB_REFUSED
            | MessageType.ECHO_JOB_NEEDS_PERSON
        ):
            return ()
        case _:
            assert_never(message_type)


def consumer_kind(consumer: ConsumerName) -> ConsumerKind:
    """`local`: one transaction, no outside call. `effect`: prepare, call
    the outside, complete, each step its own commit (plan-architecture §5)."""
    match consumer:
        case ConsumerName.PROBE_ECHO | ConsumerName.PROBE_FINISH:
            return "local"
        case ConsumerName.ECHO_SEND:
            return "effect"
        case _:
            assert_never(consumer)
