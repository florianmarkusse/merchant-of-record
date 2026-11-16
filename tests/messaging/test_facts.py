import uuid

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
    fact_type,
)
from mor.messaging.routing import consumers_for


facts = st.one_of(
    st.builds(ProbeStartedV1, probe_id=st.uuids()),
    st.builds(ProbeEchoedV1, probe_id=st.uuids()),
)


@given(fact=facts)
def test_every_fact_decodes_back_to_itself(fact: Fact) -> None:
    kind = fact_type(fact)
    assert decode(kind.message_type, kind.schema_version, encode(fact)) == fact


@given(
    message_type=st.sampled_from(MessageType),
    schema_version=st.integers(min_value=1, max_value=3),
    payload=st.binary(max_size=200),
)
def test_any_bytes_decode_to_a_fact_or_undecodable(
    message_type: MessageType, schema_version: int, payload: bytes
) -> None:
    result = decode(message_type, schema_version, payload)
    assert isinstance(result, ProbeStartedV1 | ProbeEchoedV1 | UndecodableFact)


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


def test_every_message_type_has_a_consumer() -> None:
    # A fact nobody consumes is written for nothing; today none is meant to be.
    for message_type in MessageType:
        assert len(consumers_for(message_type)) >= 1
