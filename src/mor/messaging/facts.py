"""Facts: immutable records of what happened, one per type and version.

A shape change is a new version (`ProbeStartedV2`), even an additive one,
and the old one stays decodable while prod holds undelivered messages of it.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, assert_never

import msgspec


class AggregateType(StrEnum):
    """Matches the `aggregate_type` enum type."""

    PROBE = "probe"
    ECHO_JOB = "echo_job"  # transitional, with the echo flow
    ECHO_ACCOUNT = "echo_account"  # transitional, with the echo flow
    DUTY_LOOKUP = "duty_lookup"


class MessageType(StrEnum):
    """Matches the `message_type` enum type. Past tense."""

    PROBE_STARTED = "probe_started"
    PROBE_ECHOED = "probe_echoed"
    # Transitional, with the echo flow (docs/plan-messaging.md §10).
    ECHO_JOB_REQUESTED = "echo_job_requested"
    ECHO_JOB_ECHOED = "echo_job_echoed"
    ECHO_JOB_REFUSED = "echo_job_refused"
    ECHO_JOB_NEEDS_PERSON = "echo_job_needs_person"
    ECHO_INBOX_STORED = "echo_inbox_stored"
    ECHO_VENDOR_EVENT_RECEIVED = "echo_vendor_event_received"
    ECHO_JOB_CONFIRMED = "echo_job_confirmed"
    ECHO_JOB_ARCHIVED = "echo_job_archived"
    ECHO_POLL_DUE = "echo_poll_due"
    ECHO_EVENTS_WANTED = "echo_events_wanted"
    ECHO_REPORT_DUE = "echo_report_due"
    DUTY_RATE_NEEDED = "duty_rate_needed"
    DUTY_RATE_FOUND = "duty_rate_found"
    DUTY_RATE_UNKNOWN_CODE = "duty_rate_unknown_code"
    DUTY_RATE_NEEDS_PERSON = "duty_rate_needs_person"
    DUTY_RATE_DISAGREED = "duty_rate_disagreed"


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


class EchoInboxStoredV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Raw bytes from the vendor (a webhook, a page, a file) were stored,
    not yet decoded."""

    account_id: uuid.UUID
    inbox_id: uuid.UUID


class EchoVendorEventReceivedV1(
    msgspec.Struct, frozen=True, forbid_unknown_fields=True
):
    """One event about a job: decoded from the vendor's bytes (`inbox_id`
    set), or recorded by hand (`manual`, the person is the fact's actor)."""

    echo_job_id: uuid.UUID
    vendor_event_id: str
    kind: Literal["confirmed", "archived"]
    occurred_at: datetime
    source: Literal["vendor", "manual"]
    inbox_id: uuid.UUID | None


class EchoJobConfirmedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoJobArchivedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    echo_job_id: uuid.UUID


class EchoPollDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: the time to poll a job's events came. Whether a poll is
    still wanted is decided under the job's lock."""

    echo_job_id: uuid.UUID


class EchoEventsWantedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A thin webhook said the job has news, without saying what."""

    echo_job_id: uuid.UUID


class EchoReportDueV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Scheduled: the time to look for new report files of an account."""

    account_id: uuid.UUID


class DutyRateNeededV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """A combination (vendor, destination, origin, HS code) was seen for
    the first time: ask the vendor once."""

    duty_lookup_id: uuid.UUID


class DutyRateFoundV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    duty_lookup_id: uuid.UUID


class DutyRateUnknownCodeV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor does not know the HS code."""

    duty_lookup_id: uuid.UUID


class DutyRateNeedsPersonV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor's answer could not be read."""

    duty_lookup_id: uuid.UUID
    detail: str


class DutyRateDisagreedV1(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """The vendor's rate for a charge differs from our own row's. Rates are
    exact fractions written as "numerator/denominator"."""

    duty_lookup_id: uuid.UUID
    charge: str
    vendor_rate: str
    own_rate: str
    own_line_no: int


type Fact = (
    ProbeStartedV1
    | ProbeEchoedV1
    | EchoJobRequestedV1
    | EchoJobEchoedV1
    | EchoJobRefusedV1
    | EchoJobNeedsPersonV1
    | EchoInboxStoredV1
    | EchoVendorEventReceivedV1
    | EchoJobConfirmedV1
    | EchoJobArchivedV1
    | EchoPollDueV1
    | EchoEventsWantedV1
    | EchoReportDueV1
    | DutyRateNeededV1
    | DutyRateFoundV1
    | DutyRateUnknownCodeV1
    | DutyRateNeedsPersonV1
    | DutyRateDisagreedV1
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
        case EchoInboxStoredV1():
            return FactType(MessageType.ECHO_INBOX_STORED, 1)
        case EchoVendorEventReceivedV1():
            return FactType(MessageType.ECHO_VENDOR_EVENT_RECEIVED, 1)
        case EchoJobConfirmedV1():
            return FactType(MessageType.ECHO_JOB_CONFIRMED, 1)
        case EchoJobArchivedV1():
            return FactType(MessageType.ECHO_JOB_ARCHIVED, 1)
        case EchoPollDueV1():
            return FactType(MessageType.ECHO_POLL_DUE, 1)
        case EchoEventsWantedV1():
            return FactType(MessageType.ECHO_EVENTS_WANTED, 1)
        case EchoReportDueV1():
            return FactType(MessageType.ECHO_REPORT_DUE, 1)
        case DutyRateNeededV1():
            return FactType(MessageType.DUTY_RATE_NEEDED, 1)
        case DutyRateFoundV1():
            return FactType(MessageType.DUTY_RATE_FOUND, 1)
        case DutyRateUnknownCodeV1():
            return FactType(MessageType.DUTY_RATE_UNKNOWN_CODE, 1)
        case DutyRateNeedsPersonV1():
            return FactType(MessageType.DUTY_RATE_NEEDS_PERSON, 1)
        case DutyRateDisagreedV1():
            return FactType(MessageType.DUTY_RATE_DISAGREED, 1)
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
            | EchoVendorEventReceivedV1(echo_job_id=job_id)
            | EchoJobConfirmedV1(echo_job_id=job_id)
            | EchoJobArchivedV1(echo_job_id=job_id)
            | EchoPollDueV1(echo_job_id=job_id)
            | EchoEventsWantedV1(echo_job_id=job_id)
        ):
            return Aggregate(AggregateType.ECHO_JOB, job_id)
        case (
            EchoInboxStoredV1(account_id=account_id)
            | EchoReportDueV1(account_id=account_id)
        ):
            return Aggregate(AggregateType.ECHO_ACCOUNT, account_id)
        case (
            DutyRateNeededV1(duty_lookup_id=lookup_id)
            | DutyRateFoundV1(duty_lookup_id=lookup_id)
            | DutyRateUnknownCodeV1(duty_lookup_id=lookup_id)
            | DutyRateNeedsPersonV1(duty_lookup_id=lookup_id)
            | DutyRateDisagreedV1(duty_lookup_id=lookup_id)
        ):
            return Aggregate(AggregateType.DUTY_LOOKUP, lookup_id)
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
        case MessageType.ECHO_INBOX_STORED:
            decoded = _decode(payload, EchoInboxStoredV1)
        case MessageType.ECHO_VENDOR_EVENT_RECEIVED:
            decoded = _decode(payload, EchoVendorEventReceivedV1)
        case MessageType.ECHO_JOB_CONFIRMED:
            decoded = _decode(payload, EchoJobConfirmedV1)
        case MessageType.ECHO_JOB_ARCHIVED:
            decoded = _decode(payload, EchoJobArchivedV1)
        case MessageType.ECHO_POLL_DUE:
            decoded = _decode(payload, EchoPollDueV1)
        case MessageType.ECHO_EVENTS_WANTED:
            decoded = _decode(payload, EchoEventsWantedV1)
        case MessageType.ECHO_REPORT_DUE:
            decoded = _decode(payload, EchoReportDueV1)
        case MessageType.DUTY_RATE_NEEDED:
            decoded = _decode(payload, DutyRateNeededV1)
        case MessageType.DUTY_RATE_FOUND:
            decoded = _decode(payload, DutyRateFoundV1)
        case MessageType.DUTY_RATE_UNKNOWN_CODE:
            decoded = _decode(payload, DutyRateUnknownCodeV1)
        case MessageType.DUTY_RATE_NEEDS_PERSON:
            decoded = _decode(payload, DutyRateNeedsPersonV1)
        case MessageType.DUTY_RATE_DISAGREED:
            decoded = _decode(payload, DutyRateDisagreedV1)
        case _:
            assert_never(message_type)
    if isinstance(decoded, str):
        return UndecodableFact(message_type, schema_version, "invalid_payload", decoded)
    return decoded
