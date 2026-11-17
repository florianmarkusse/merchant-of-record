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
