"""Facts: immutable records of what happened, one per type and version.

A shape change is a new version (`ProbeStartedV2`), even an additive one,
and the old one stays decodable while prod holds undelivered messages of it.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, assert_never

import msgspec


class AggregateType(StrEnum):
    """Matches the `aggregate_type` enum type."""

    PROBE = "probe"
    ECHO_JOB = "echo_job"  # transitional, with the echo flow


class MessageType(StrEnum):
    """Matches the `message_type` enum type. Past tense."""

    PROBE_STARTED = "probe_started"
    PROBE_ECHOED = "probe_echoed"
    # Transitional, with the echo flow (docs/plan-messaging.md §10).
    ECHO_JOB_REQUESTED = "echo_job_requested"
    ECHO_JOB_ECHOED = "echo_job_echoed"
    ECHO_JOB_REFUSED = "echo_job_refused"
    ECHO_JOB_NEEDS_PERSON = "echo_job_needs_person"


@dataclass(frozen=True)
class Aggregate:
    type: AggregateType
    id: uuid.UUID


class ProbeStartedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


class ProbeEchoedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


class EchoJobRequestedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoJobEchoedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID
    vendor_echo_id: str


class EchoJobRefusedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID
    reason: str


class EchoJobNeedsPersonV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A send whose answer was lost, on a vendor that promises nothing about
    a resend, and the vendor does not know it: a person decides."""

    echo_job_id: uuid.UUID


type Fact = (
    ProbeStartedV1
    | ProbeEchoedV1
    | EchoJobRequestedV1
    | EchoJobEchoedV1
    | EchoJobRefusedV1
    | EchoJobNeedsPersonV1
)


@dataclass(frozen=True)
class FactType:
    message_type: MessageType
    schema_version: int


@dataclass(frozen=True)
class UndecodableFact:
    message_type: MessageType
    schema_version: int
    reason: Literal["unknown_version", "invalid_payload"]
    detail: str


def fact_type(fact: Fact) -> FactType:
    match fact:
        case ProbeStartedV1():
            return FactType(MessageType.PROBE_STARTED, 1)
        case ProbeEchoedV1():
            return FactType(MessageType.PROBE_ECHOED, 1)
        case EchoJobRequestedV1():
            return FactType(MessageType.ECHO_JOB_REQUESTED, 1)
        case EchoJobEchoedV1():
            return FactType(MessageType.ECHO_JOB_ECHOED, 1)
        case EchoJobRefusedV1():
            return FactType(MessageType.ECHO_JOB_REFUSED, 1)
        case EchoJobNeedsPersonV1():
            return FactType(MessageType.ECHO_JOB_NEEDS_PERSON, 1)
        case _:
            assert_never(fact)


def aggregate_of(fact: Fact) -> Aggregate:
    """The aggregate a fact belongs to; its order is kept per aggregate."""
    match fact:
        case ProbeStartedV1(probe_id=probe_id) | ProbeEchoedV1(probe_id=probe_id):
            return Aggregate(AggregateType.PROBE, probe_id)
        case (
            EchoJobRequestedV1(echo_job_id=job_id)
            | EchoJobEchoedV1(echo_job_id=job_id)
            | EchoJobRefusedV1(echo_job_id=job_id)
            | EchoJobNeedsPersonV1(echo_job_id=job_id)
        ):
            return Aggregate(AggregateType.ECHO_JOB, job_id)
        case _:
            assert_never(fact)


def encode(fact: Fact) -> bytes:
    return msgspec.json.encode(fact)


def _decode[T](payload: bytes, record: type[T]) -> T | str:
    try:
        return msgspec.json.decode(payload, type=record)
    except msgspec.DecodeError as error:
        return str(error)


def decode(
    message_type: MessageType, schema_version: int, payload: bytes
) -> Fact | UndecodableFact:
    """Bytes from the outbox back into the record they were written from.
    Every type has only version 1 so far."""
    if schema_version != 1:
        return UndecodableFact(message_type, schema_version, "unknown_version", "")
    decoded: Fact | str
    match message_type:
        case MessageType.PROBE_STARTED:
            decoded = _decode(payload, ProbeStartedV1)
        case MessageType.PROBE_ECHOED:
            decoded = _decode(payload, ProbeEchoedV1)
        case MessageType.ECHO_JOB_REQUESTED:
            decoded = _decode(payload, EchoJobRequestedV1)
        case MessageType.ECHO_JOB_ECHOED:
            decoded = _decode(payload, EchoJobEchoedV1)
        case MessageType.ECHO_JOB_REFUSED:
            decoded = _decode(payload, EchoJobRefusedV1)
        case MessageType.ECHO_JOB_NEEDS_PERSON:
            decoded = _decode(payload, EchoJobNeedsPersonV1)
        case _:
            assert_never(message_type)
    if isinstance(decoded, str):
        return UndecodableFact(message_type, schema_version, "invalid_payload", decoded)
    return decoded
