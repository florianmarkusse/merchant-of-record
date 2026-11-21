"""The ways a call to a vendor can fail, the same for every port
(plan-architecture §5). Each port's result unions end in `CallFailure`.

What a failure means is here; what to do about it (send again, look up,
hand to a person) is the operation's duplicate guard, in its module."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Unavailable:
    """The request provably never reached the vendor (a refused
    connection): safe to send again whatever the guard."""

    detail: str


@dataclass(frozen=True)
class OutcomeUnknown:
    """No answer (a timeout, a dropped connection, a 5xx): the vendor may
    have done the work. The duplicate guard decides what happens next; a
    read is always safe to ask again."""

    detail: str


@dataclass(frozen=True)
class UndecodableBody:
    """An answer that is not what the vendor's API documents."""

    detail: str


type CallFailure = Unavailable | OutcomeUnknown | UndecodableBody
