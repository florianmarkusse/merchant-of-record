"""The echo port: our records and result unions, no vendor words.

Transitional, like the whole echo flow (docs/plan-messaging.md §10): it
proves the effect machinery and goes once the fake PSP exercises the same
machinery (slice 5), unless it serves something else by then.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Literal


class EchoProvider(StrEnum):
    """Matches the `echo_provider` enum type. One fake flavour per duplicate
    guard, so every guard is exercised (plan-architecture.md §6)."""

    FAKE_KEYED = "fake_keyed"
    FAKE_CANCELLABLE = "fake_cancellable"
    FAKE_PLAIN = "fake_plain"


@dataclass(frozen=True)
class EchoAccount:
    id: uuid.UUID
    provider: EchoProvider
    base_url: str
    timeout: timedelta


@dataclass(frozen=True)
class SendRequest:
    reference: str  # our echo_job id: what find_by_reference searches
    message: str
    idempotency_key: str


@dataclass(frozen=True)
class VendorEcho:
    echo_id: str
    reference: str
    state: Literal["live", "cancelled"]


@dataclass(frozen=True)
class Sent:
    echo_id: str


@dataclass(frozen=True)
class Found:
    echoes: tuple[VendorEcho, ...]


@dataclass(frozen=True)
class Cancelled:
    echo_id: str


@dataclass(frozen=True)
class Refused:
    """A documented refusal: the vendor did nothing and says why."""

    reason: str


@dataclass(frozen=True)
class Unavailable:
    """The request provably never reached the vendor (connection refused):
    safe to send again whatever the guard."""

    detail: str


@dataclass(frozen=True)
class OutcomeUnknown:
    """No answer (timeout, dropped connection, a 5xx): the vendor may have
    done the work. The duplicate guard decides what happens next."""

    detail: str


type SendResult = Sent | Refused | Unavailable | OutcomeUnknown
type FindResult = Found | Unavailable | OutcomeUnknown
type CancelResult = Cancelled | Refused | Unavailable | OutcomeUnknown
