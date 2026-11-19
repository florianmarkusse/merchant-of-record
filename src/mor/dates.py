from datetime import UTC, date, datetime


_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip


def format_moment(moment: datetime) -> str:
    """The one format for a moment a person reads (pages, logs, emails):
    `16-Nov-2026 09:30 UTC`. Month names from our own table, never the
    process locale."""
    utc = moment.astimezone(UTC)
    return f"{utc.day:02d}-{_MONTHS[utc.month - 1]}-{utc.year} {utc:%H:%M} UTC"


def format_day(day: date) -> str:
    """The one format for a date a person reads: `16-Nov-2026`."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


def format_instant(moment: datetime) -> str:
    """The one format for a moment a machine reads (API payloads, files we
    write): RFC 3339 in UTC with its offset written, to the second,
    `2026-11-16T09:30:00Z`. Never a naive time: one would be read in the
    process's local zone without saying so."""
    assert moment.tzinfo is not None, "a naive datetime leaving the process"
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
