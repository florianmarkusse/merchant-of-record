from dataclasses import dataclass


@dataclass(frozen=True)
class Applied:
    """The consumer changed its aggregate."""


@dataclass(frozen=True)
class Stale:
    """The aggregate had already moved past this fact (a redelivery); the
    consumer changed nothing."""


type ConsumerOutcome = Applied | Stale
