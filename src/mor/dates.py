from datetime import UTC, datetime


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
