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

from mor.psp.port import PspEventKind


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


def _ended(kind: PspEventKind) -> AttemptState | Stale:
    match kind:
        case PspEventKind.DECLINED:
            return AttemptState.DECLINED
        case PspEventKind.EXPIRED:
            return AttemptState.EXPIRED
        case PspEventKind.CANCELLED:
            return AttemptState.CANCELLED
        case PspEventKind.AUTHORISED | PspEventKind.CAPTURED:
            return Stale()


def after_event(state: AttemptState, kind: PspEventKind) -> AttemptState | Stale:
    match state:
        case AttemptState.REQUESTED | AttemptState.ACTION_REQUIRED:
            match kind:
                case PspEventKind.AUTHORISED:
                    return AttemptState.AUTHORISED
                case PspEventKind.CAPTURED:
                    return AttemptState.CAPTURED
                case (
                    PspEventKind.DECLINED
                    | PspEventKind.EXPIRED
                    | PspEventKind.CANCELLED
                ):
                    return _ended(kind)
        case AttemptState.AUTHORISED:
            match kind:
                case PspEventKind.CAPTURED:
                    return AttemptState.CAPTURED
                case PspEventKind.AUTHORISED | PspEventKind.DECLINED:
                    return Stale()
                case PspEventKind.EXPIRED | PspEventKind.CANCELLED:
                    return _ended(kind)
        case AttemptState.CANCELLING:
            match kind:
                case PspEventKind.CAPTURED:
                    return AttemptState.CAPTURED  # a payment made is honoured
                case PspEventKind.AUTHORISED:
                    return Stale()  # still cancelling: never captured
                case (
                    PspEventKind.DECLINED
                    | PspEventKind.EXPIRED
                    | PspEventKind.CANCELLED
                ):
                    return _ended(kind)
        case AttemptState.DECLINED | AttemptState.EXPIRED | AttemptState.CANCELLED:
            match kind:
                case PspEventKind.CAPTURED:
                    # Money taken on an attempt the PSP had ended: never
                    # booked by itself.
                    return AttemptState.NEEDS_PERSON
                case (
                    PspEventKind.AUTHORISED
                    | PspEventKind.DECLINED
                    | PspEventKind.EXPIRED
                    | PspEventKind.CANCELLED
                ):
                    return Stale()
        case AttemptState.CAPTURED | AttemptState.NEEDS_PERSON:
            return Stale()
