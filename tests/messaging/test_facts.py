import uuid
from typing import get_args

from hypothesis import given
from hypothesis import strategies as st

from mor.messaging.facts import (
    Aggregate,
    AggregateType,
    Fact,
    MessageType,
    ProbeEchoedV1,
    ProbeStartedV1,
    UndecodableFact,
    aggregate_of,
    decode,
    encode,
)


FACT_RECORDS: tuple[type[Fact], ...] = get_args(Fact.__value__)

facts = st.one_of([st.from_type(record) for record in FACT_RECORDS])


@given(fact=facts)
def test_every_fact_decodes_back_to_itself_by_its_own_type_and_version(
    fact: Fact,
) -> None:
    assert decode(fact.message_type, fact.schema_version, encode(fact)) == fact


def test_every_message_type_is_named_by_one_record() -> None:
    named: list[MessageType] = []
    for record in FACT_RECORDS:
        named.append(record.message_type)
    assert sorted(named) == sorted(MessageType)


@given(
    message_type=st.sampled_from(MessageType),
    schema_version=st.integers(min_value=1, max_value=3),
    payload=st.binary(max_size=200),
)
def test_any_bytes_decode_to_a_fact_or_undecodable(
    message_type: MessageType, schema_version: int, payload: bytes
) -> None:
    result = decode(message_type, schema_version, payload)
    assert isinstance(result, (*FACT_RECORDS, UndecodableFact))


def test_unknown_field_or_version_is_undecodable() -> None:
    probe_id = uuid.uuid7()
    extra = b'{"probe_id": "%s", "note": "x"}' % str(probe_id).encode()
    result = decode(MessageType.PROBE_STARTED, 1, extra)
    assert isinstance(result, UndecodableFact)
    assert result.reason == "invalid_payload"
    payload = encode(ProbeStartedV1(probe_id=probe_id))
    result = decode(MessageType.PROBE_STARTED, 2, payload)
    assert isinstance(result, UndecodableFact)
    assert result.reason == "unknown_version"


def test_probe_facts_belong_to_their_probe() -> None:
    probe_id = uuid.uuid7()
    assert aggregate_of(ProbeEchoedV1(probe_id=probe_id)) == Aggregate(
        AggregateType.PROBE, probe_id
    )
