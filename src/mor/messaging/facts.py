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


class MessageType(StrEnum):
    """Matches the `message_type` enum type. Past tense."""

    PROBE_STARTED = "probe_started"
    PROBE_ECHOED = "probe_echoed"


@dataclass(frozen=True)
class Aggregate:
    type: AggregateType
    id: uuid.UUID


class ProbeStartedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


class ProbeEchoedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    probe_id: uuid.UUID


type Fact = ProbeStartedV1 | ProbeEchoedV1


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
        case _:
            assert_never(fact)


def aggregate_of(fact: Fact) -> Aggregate:
    """The aggregate a fact belongs to; its order is kept per aggregate."""
    match fact:
        case ProbeStartedV1(probe_id=probe_id) | ProbeEchoedV1(probe_id=probe_id):
            return Aggregate(AggregateType.PROBE, probe_id)
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
    """Bytes from the outbox back into the record they were written from."""
    decoded: Fact | str
    match message_type:
        case MessageType.PROBE_STARTED:
            if schema_version != 1:
                return UndecodableFact(
                    message_type, schema_version, "unknown_version", ""
                )
            decoded = _decode(payload, ProbeStartedV1)
        case MessageType.PROBE_ECHOED:
            if schema_version != 1:
                return UndecodableFact(
                    message_type, schema_version, "unknown_version", ""
                )
            decoded = _decode(payload, ProbeEchoedV1)
        case _:
            assert_never(message_type)
    if isinstance(decoded, str):
        return UndecodableFact(message_type, schema_version, "invalid_payload", decoded)
    return decoded
