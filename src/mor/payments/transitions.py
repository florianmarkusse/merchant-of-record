"""The payment attempt's states and what a PSP event does to each
(docs/plan-payments.md §4). Pure: every (state, event) pair has an answer,
a new state or `Stale`, and a property checks the table is total.

Events may arrive twice, late or out of order (a webhook racing the
create call's answer, an old event redelivered after a new one), so the
table only ever moves forward:

    requested, action_required ─authorised─▶ authorised ─captured─▶ captured
                     │                           │
                     └─declined / expired / cancelled─▶ (that end)
    cancelling: the buyer chose another way; any end applies, and a
                capture is honoured
    a capture on an attempt we had already ended ─▶ needs_person
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from mor.psp.port import EventKind


class AttemptState(StrEnum):
    """Matches the `payment_attempt_state` enum type."""

    REQUESTED = "requested"
    ACTION_REQUIRED = "action_required"
    AUTHORISED = "authorised"
    CANCELLING = "cancelling"
    CAPTURED = "captured"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    NEEDS_PERSON = "needs_person"


# Not final: a session with one of these is `paying`, and only one may
# exist per session (the partial unique index).
OPEN_STATES = (
    AttemptState.REQUESTED,
    AttemptState.ACTION_REQUIRED,
    AttemptState.AUTHORISED,
    AttemptState.CANCELLING,
)


@dataclass(frozen=True)
class Stale:
    """The attempt is already past what this event says."""


def _ended(kind: EventKind) -> AttemptState | Stale:
    match kind:
        case "declined":
            return AttemptState.DECLINED
        case "expired":
            return AttemptState.EXPIRED
        case "cancelled":
            return AttemptState.CANCELLED
        case "authorised" | "captured":
            return Stale()
        case _:
            assert_never(kind)


def after_event(state: AttemptState, kind: EventKind) -> AttemptState | Stale:
    match state:
        case AttemptState.REQUESTED | AttemptState.ACTION_REQUIRED:
            match kind:
                case "authorised":
                    return AttemptState.AUTHORISED
                case "captured":
                    return AttemptState.CAPTURED
                case "declined" | "expired" | "cancelled":
                    return _ended(kind)
                case _:
                    assert_never(kind)
        case AttemptState.AUTHORISED:
            match kind:
                case "captured":
                    return AttemptState.CAPTURED
                case "authorised" | "declined":
                    return Stale()
                case "expired" | "cancelled":
                    return _ended(kind)
                case _:
                    assert_never(kind)
        case AttemptState.CANCELLING:
            match kind:
                case "captured":
                    return AttemptState.CAPTURED  # a payment made is honoured
                case "authorised":
                    return Stale()  # still cancelling: never captured
                case "declined" | "expired" | "cancelled":
                    return _ended(kind)
                case _:
                    assert_never(kind)
        case AttemptState.DECLINED | AttemptState.EXPIRED | AttemptState.CANCELLED:
            match kind:
                case "captured":
                    # Money taken on an attempt the PSP had ended: never
                    # booked by itself.
                    return AttemptState.NEEDS_PERSON
                case "authorised" | "declined" | "expired" | "cancelled":
                    return Stale()
                case _:
                    assert_never(kind)
        case AttemptState.CAPTURED | AttemptState.NEEDS_PERSON:
            return Stale()
        case _:
            assert_never(state)
