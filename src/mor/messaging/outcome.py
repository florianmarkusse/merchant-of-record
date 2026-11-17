from dataclasses import dataclass


@dataclass(frozen=True)
class Applied:
    """The consumer changed its aggregate."""


@dataclass(frozen=True)
class Stale:
    """The aggregate had already moved past this fact (a redelivery); the
    consumer changed nothing."""


type ConsumerOutcome = Applied | Stale


@dataclass(frozen=True)
class NoCall:
    """An effect consumer's `prepare` found nothing to ask the vendor: the
    delivery is done with this outcome."""

    outcome: ConsumerOutcome


@dataclass(frozen=True)
class Done:
    """`complete` finished the delivery."""

    outcome: ConsumerOutcome


@dataclass(frozen=True)
class RetryLater:
    """`complete` could not finish (Unavailable, OutcomeUnknown): the
    delivery stays open and is tried again after a backoff."""

    reason: str


@dataclass(frozen=True)
class Again:
    """`complete` recorded a step and there is more to ask the vendor now
    (cancelling a duplicate): the worker runs `prepare` again at once."""


type StepResult = Done | RetryLater | Again
