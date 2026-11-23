"""Payment closed sets apart from the flow, so `mor.db.enums` can load them
(the flow imports the database)."""

from enum import StrEnum


class PspOperation(StrEnum):
    """Matches the `psp_operation` enum type: a call that may do work at the
    PSP, one `psp_request` row each."""

    CREATE = "create"
    CAPTURE = "capture"
    CANCEL = "cancel"
