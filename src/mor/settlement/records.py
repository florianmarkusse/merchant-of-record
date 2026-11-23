"""Settlement closed sets apart from the store, so `mor.db.enums` can load
them (the store imports the database)."""

from enum import StrEnum


class LineState(StrEnum):
    """Matches the `settlement_line_state` enum type."""

    RECEIVED = "received"
    WAITING = "waiting"  # its order does not exist yet
    BOOKED = "booked"
    EXCEPTION = "exception"
    COUNTED = "counted"  # a balance transfer: books nothing, counts


class ExceptionKind(StrEnum):
    """Matches the `settlement_exception_kind` enum type: what did not fit,
    for a person."""

    UNKNOWN_REFERENCE = "unknown_reference"
    NOT_CAPTURED = "not_captured"
    AMOUNT_DIFFERS = "amount_differs"
    CURRENCY_DIFFERS = "currency_differs"
    SETTLED_TWICE = "settled_twice"
    UNKNOWN_TYPE = "unknown_type"


class Reconciliation(StrEnum):
    """Matches `settlement_reconciliation`: does a payout's net add up to the
    lines that have arrived for it? Shown, not raised (answer 1 of
    23-Nov-2026)."""

    OPEN = "open"
    BALANCED = "balanced"


class ReadPhase(StrEnum):
    """Matches `psp_settlement_read_phase`: in `api` mode, whether the payout
    being read is waiting for its object to say its lines are listable, or
    has its lines being read."""

    PAYOUT = "payout"
    LINES = "lines"
