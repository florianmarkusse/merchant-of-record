"""The echo job's states, apart from its flow so `mor.db.enums` can load
them (the flow imports the database). Transitional with the echo flow
(docs/plan-messaging.md §10)."""

from enum import StrEnum


class EchoJobState(StrEnum):
    """Matches the `echo_job_state` enum type."""

    REQUESTED = "requested"
    DEDUPLICATING = "deduplicating"
    ECHOED = "echoed"
    REFUSED = "refused"
    NEEDS_PERSON = "needs_person"
