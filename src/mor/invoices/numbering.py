"""The numbering series, which one an invoice is numbered in, and how its
number is written (docs/plan-payments.md §6.1). A series is plain data
here; its counter is a row of `invoice_series`, inserted on first use and
taken under its lock in `mor.invoices.store`.

To grow: a per-country series an adviser asks for is an entry in `SERIES`
and one in `SERIES_BY_COUNTRY`; credit notes (slice 8) get a series of
their own. A series' code is never reused for another: its counter runs
on. Issued invoices keep the number text they were issued with, so a new
format never rewrites one.
"""

from datetime import datetime

from mor.entity import SellingEntity
from mor.invoices.records import Series


SERIES: dict[str, Series] = {
    "INV": Series(
        code="INV", entity="placeholder", prefix="INV", with_year=True, pad_width=6
    ),
}

# Each entity's default series, by `SellingEntity.code`.
DEFAULT_SERIES: dict[str, str] = {"placeholder": "INV"}

# (entity code, buyer country) -> series, where a country needs its own.
# None yet (answered 19-Nov-2026: one series per entity until an adviser
# says otherwise).
SERIES_BY_COUNTRY: dict[tuple[str, str], str] = {}


def series_for(entity: SellingEntity, buyer_country: str) -> str:
    """The one place a series is picked."""
    country_series = SERIES_BY_COUNTRY.get((entity.code, buyer_country))
    if country_series is not None:
        return country_series
    return DEFAULT_SERIES[entity.code]


def number_text(series: Series, number: int, issued_at: datetime) -> str:
    """`INV-2026-000001`: the prefix, the issue year if the series shows
    it, and the running number, which does not restart each year."""
    counter = str(number).zfill(series.pad_width)
    if series.with_year:
        return f"{series.prefix}-{issued_at.year}-{counter}"
    return f"{series.prefix}-{counter}"
