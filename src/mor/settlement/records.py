"""Settlement closed sets apart from the store, so `mor.db.enums` can load
them (the store imports the database)."""

from enum import StrEnum


class LineState(StrEnum):
    """Matches the `settlement_line_state` enum type."""

    RECEIVED = "received"
    WAITING = "waiting"  # its order does not exist yet
    BOOKED = "booked"
    EXCEPTION = "exception"


class ExceptionKind(StrEnum):
    """Matches the `settlement_exception_kind` enum type: what did not fit,
    for a person."""

    UNKNOWN_REFERENCE = "unknown_reference"
    NOT_CAPTURED = "not_captured"
    AMOUNT_DIFFERS = "amount_differs"
    CURRENCY_DIFFERS = "currency_differs"
    SETTLED_TWICE = "settled_twice"
    UNKNOWN_TYPE = "unknown_type"
    NET_DIFFERS = "net_differs"
