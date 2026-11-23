"""Order closed sets apart from the flow, so `mor.db.enums` can load them
(the flow imports the database)."""

from enum import StrEnum


class OrderState(StrEnum):
    """Matches the `order_state` enum type. Refunds (slice 8) add more."""

    PAID = "paid"
