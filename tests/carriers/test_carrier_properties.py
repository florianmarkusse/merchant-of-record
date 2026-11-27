"""The fake carrier client's tracking decoders (docs/plan-shipments.md §7,
§9): any bytes give our records or a typed error, never a raise; a
well-formed event keeps the carrier's code, maps a known one to its kind
and any other to `other`."""

import json
from datetime import UTC, datetime, timedelta

from hypothesis import given, strategies

from mor.carriers.fake_client import EVENT_KINDS, decode_events, decode_thin_webhook
from mor.carriers.port import CarrierEvent, ShipmentEventKind, ThinNotice
from mor.vendor_failures import UndecodableBody


@given(body=strategies.binary(max_size=400))
def test_any_bytes_decode_to_events_or_a_typed_error(body: bytes) -> None:
    decoded = decode_events(body)
    if not isinstance(decoded, UndecodableBody):
        for event in decoded:
            assert isinstance(event, CarrierEvent)


@given(body=strategies.binary(max_size=400))
def test_any_bytes_decode_to_a_thin_notice_or_a_typed_error(body: bytes) -> None:
    decoded = decode_thin_webhook(body)
    assert isinstance(decoded, ThinNotice | UndecodableBody)


@given(
    code=strategies.one_of(
        strategies.sampled_from(sorted(EVENT_KINDS)), strategies.text(max_size=30)
    ),
    hour=strategies.integers(min_value=0, max_value=23),
)
def test_an_event_keeps_its_code_and_maps_only_known_ones(code: str, hour: int) -> None:
    body = json.dumps(
        {
            "events": [
                {
                    "id": "TE-1",
                    "tracking_number": "TN-1",
                    "code": code,
                    "occurred_at": f"2026-11-18T{hour:02d}:00:00+07:00",
                }
            ]
        }
    ).encode()
    decoded = decode_events(body)
    assert not isinstance(decoded, UndecodableBody), decoded
    (event,) = decoded
    assert event.carrier_code == code
    assert event.kind == EVENT_KINDS.get(code, ShipmentEventKind.OTHER)
    # The same moment, in UTC.
    assert event.occurred_at.tzinfo == UTC
    in_utc = datetime(2026, 11, 18, hour, tzinfo=UTC) - timedelta(hours=7)
    assert event.occurred_at == in_utc
