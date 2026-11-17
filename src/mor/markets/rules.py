"""What one load says about a country on a day (docs/plan-quote.md §2): is
it open for checkout, and which landed rates may apply, most specific
first. Pure: the set is passed in.

The band (always, up to or above a limit) needs the order's value, so it
is decided by the tax path rule (step 4b), which walks these candidates in
order and takes the first whose band holds the value, as
`scripts/markets.py` does.
"""

from dataclasses import dataclass
from datetime import date

from mor.markets.records import (
    USABLE_STATUSES,
    Charge,
    Country,
    LandedRate,
    MarketSet,
    MarketStatus,
)


@dataclass(frozen=True)
class NotListed:
    iso2: str


@dataclass(frozen=True)
class NotOpen:
    """The country's market status, and the note shown to the buyer."""

    status: MarketStatus
    note: str


@dataclass(frozen=True)
class RulesNotConfirmed:
    """A rate a quote would rely on is weaker than verified or secondary
    (decided 17-Nov-2026, Q1): closed rather than charging on a guess."""

    line_nos: tuple[int, ...]


type Closed = NotListed | NotOpen | RulesNotConfirmed


@dataclass(frozen=True)
class Open:
    country: Country
    # Per charge, the rates in force on the day, most specific first.
    candidates: dict[Charge, tuple[LandedRate, ...]]


def country_rules(
    market: MarketSet, iso2: str, category: str, on: date
) -> Open | Closed:
    country = _country(market, iso2)
    if country is None:
        return NotListed(iso2)
    if country.status != MarketStatus.OPEN:
        return NotOpen(country.status, country.status_note)
    candidates: dict[Charge, tuple[LandedRate, ...]] = {}
    unconfirmed: list[int] = []
    for charge in Charge:
        rates = candidate_rates(market, iso2, category, charge, on)
        if rates:
            candidates[charge] = rates
        for rate in rates:
            if rate.status not in USABLE_STATUSES:
                unconfirmed.append(rate.line_no)
    if unconfirmed:
        return RulesNotConfirmed(tuple(sorted(unconfirmed)))
    return Open(country, candidates)


def candidate_rates(
    market: MarketSet, iso2: str, category: str, charge: Charge, on: date
) -> tuple[LandedRate, ...]:
    """The rates for a charge in force on `on`, most specific first: the
    country before its groups, and within each the category's own rows
    before rows for every category."""
    places: list[tuple[str | None, str | None]] = [(iso2, None)]
    for group in market.groups:
        if iso2 in group.members:
            places.append((None, group.code))
    ordered: list[LandedRate] = []
    for country_code, group_code in places:
        for wanted in (category, None):
            matching: list[LandedRate] = []
            for rate in market.landed_rates:
                if (
                    rate.scope.country == country_code
                    and rate.scope.group == group_code
                    and rate.category == wanted
                    and rate.charge == charge
                ):
                    matching.append(rate)
            ordered.extend(_in_force(matching, on))
    return tuple(ordered)


def _in_force(rates: list[LandedRate], on: date) -> list[LandedRate]:
    """Of rates with the same collector and band (one place, category and
    charge), the latest one started on or before `on`; rates not yet started
    are left out. A newer row replaces the older from its date whether its
    limit or its rate changed: that is how a law change is entered (the old
    row stays, the new one has the date)."""
    latest: dict[tuple[object, ...], LandedRate] = {}
    order: list[tuple[object, ...]] = []
    for rate in rates:
        if rate.effective_from is not None and rate.effective_from > on:
            continue
        key = (rate.collector, rate.band)
        current = latest.get(key)
        if current is None:
            order.append(key)
            latest[key] = rate
        elif _started(rate) > _started(current):
            latest[key] = rate
    kept: list[LandedRate] = []
    for key in order:
        kept.append(latest[key])
    return kept


def _started(rate: LandedRate) -> date:
    return rate.effective_from if rate.effective_from is not None else date.min


def _country(market: MarketSet, iso2: str) -> Country | None:
    for country in market.countries:
        if country.iso2 == iso2:
            return country
    return None
