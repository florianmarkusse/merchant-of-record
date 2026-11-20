from datetime import UTC, datetime, timedelta, timezone

from mor.dates import format_moment, parse_instant


def test_moment_reads_as_day_month_year_in_utc() -> None:
    assert format_moment(datetime(2027, 1, 1, 14, 35, tzinfo=UTC)) == (
        "01-Jan-2027 14:35 UTC"
    )


def test_moment_in_another_zone_is_shown_in_utc() -> None:
    bangkok = timezone(timedelta(hours=7))
    assert format_moment(datetime(2026, 11, 17, 2, 5, tzinfo=bangkok)) == (
        "16-Nov-2026 19:05 UTC"
    )


def test_an_instant_with_an_offset_is_read_in_utc() -> None:
    moment = parse_instant("2026-11-18T16:30:00+07:00")
    assert moment == datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
    assert moment is not None and moment.tzinfo == UTC


def test_an_instant_without_an_offset_is_refused() -> None:
    assert parse_instant("2026-11-18T16:30:00") is None
    assert parse_instant("yesterday") is None
