"""Moments and days: what a person reads, what a machine reads, and what
comes in. Inside the process every moment is aware and in UTC: inbound ones
are converted where they are decoded (`AwareDatetime`, `parse_instant`),
and the database hands them back in UTC (`mor.db.connect`)."""

from datetime import UTC, date, datetime
from typing import Annotated

import msgspec


_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_moment(moment: datetime) -> str:
    """The one format for a moment a person reads (pages, logs, emails):
    `16-Nov-2026 09:30 UTC`. Month names from our own table, never the
    process locale."""
    utc = moment.astimezone(UTC)
    return f"{utc.day:02d}-{_MONTHS[utc.month - 1]}-{utc.year} {utc:%H:%M} UTC"


# For a datetime field of a record decoded at the edge: msgspec refuses one
# without an offset, so a vendor's local time is never taken for ours. The
# decoder then converts it with `to_utc`.
type AwareDatetime = Annotated[datetime, msgspec.Meta(tz=True)]


def to_utc(moment: datetime) -> datetime:
    """An aware moment, in UTC. A naive one is a bug: it would be read in
    the process's local zone without saying so."""
    assert moment.tzinfo is not None, "a naive datetime inside the process"
    return moment.astimezone(UTC)


def parse_instant(text: str) -> datetime | None:
    """An ISO 8601 moment from text a vendor wrote (a CSV cell), in UTC, or
    None if it is not one or names no offset."""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


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
