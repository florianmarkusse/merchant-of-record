"""The probe's states, apart from its flow so `mor.db.enums` can load them
(the flow imports the database)."""

from enum import StrEnum


class ProbeState(StrEnum):
    """Matches the `probe_state` enum type."""

    STARTED = "started"
    ECHOED = "echoed"
    FINISHED = "finished"
