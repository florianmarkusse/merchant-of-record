from hypothesis import example, given
from hypothesis import strategies as st

from mor.echo.events import VendorStatus, derive_status
from mor.echo.fake_client import decode_full_webhook, decode_report, decode_thin_webhook
from mor.echo.port import EventKind, UndecodableBody


@given(
    kinds=st.lists(st.sampled_from(EventKind), min_size=1, max_size=8), data=st.data()
)
def test_status_is_the_same_for_any_order_or_duplication(
    kinds: list[EventKind], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(kinds))
    repeats = data.draw(st.lists(st.sampled_from(kinds), max_size=4))
    assert derive_status(shuffled) == derive_status(kinds)
    assert derive_status(kinds + repeats) == derive_status(kinds)


def test_status_follows_the_furthest_event() -> None:
    assert derive_status([]) == VendorStatus.NONE
    assert derive_status([EventKind.CONFIRMED]) == VendorStatus.CONFIRMED
    assert derive_status([EventKind.CONFIRMED, EventKind.ARCHIVED]) == (
        VendorStatus.ARCHIVED
    )
    # Archived without its confirmed (lost, or still on its way).
    assert derive_status([EventKind.ARCHIVED]) == VendorStatus.ARCHIVED


@given(body=st.binary(max_size=300))
@example(body=b"\r\x00")  # once raised csv.Error from the report decoder
def test_every_vendor_decoder_turns_any_bytes_into_events_or_an_error(
    body: bytes,
) -> None:
    for result in [
        decode_full_webhook(body),
        decode_report(body),
    ]:
        assert isinstance(result, tuple | UndecodableBody)
    assert isinstance(decode_thin_webhook(body), str | UndecodableBody)


def test_a_report_without_a_zone_on_its_times_is_undecodable() -> None:
    body = (
        b"event_id,echo_id,reference,kind,occurred_at\n"
        b"evt_1,echo_1,ref,confirmed,2026-11-17T09:30:00\n"
    )
    assert isinstance(decode_report(body), UndecodableBody)
