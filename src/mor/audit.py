from dataclasses import dataclass
from enum import StrEnum


class ActorKind(StrEnum):
    """Who or what made a change; matches the `actor_kind` enum type."""

    SYSTEM = "system"
    STAFF = "staff"
    MERCHANT_API_KEY = "merchant_api_key"


@dataclass(frozen=True)
class Actor:
    """Passed into every write, so each change records who made it."""

    kind: ActorKind
    id: str
