"""The rate for one line's product, per HS code (docs/plan-quote.md §3).
Pure.

Rows for the destination (its own before its groups') are matched by HS
code prefix; the longest prefix with a standard rate wins, and within it a
row for the line's origin before an any-origin row. A trade agreement's
rate for the line's origin applies when every condition it names is met
and its research status is usable; otherwise the standard rate applies and
the result says which agreement was missed and why.

VAT falls back to the deciding band row's percentage when no HS row
matches. Duty never falls back to a category percentage: a flat duty from
the band row (the EU's EUR 3 per line) needs no HS code, but a percentage
does, and without a matching row the result is `RateNeeded`.

Who decides is the destination's duty mode (4b-2): `own_only` is the rule
above; `vendor` takes the vendor's stored answer for exactly this
(destination, origin, HS code); `own_first` takes our row when it has a
usable one and the vendor's answer otherwise. A vendor answer is used only
when it is a plain percentage without a note (a note may be a preference
the vendor applied without checking our conditions).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.markets.records import (
    USABLE_STATUSES,
    Charge,
    DutyMode,
    DutySource,
    HsRate,
    LandedRate,
    MarketSet,
    Percent,
    PreferenceCondition,
)


@dataclass(frozen=True)
class Line:
    hs_code: str | None  # digits only
    origin: str
    # Agreement conditions this product meets. Recorded with the merchant
    # records (4d); until then none, so no preference applies.
    conditions_met: frozenset[PreferenceCondition]


@dataclass(frozen=True)
class MissedPreference:
    rate: HsRate
    missing: frozenset[PreferenceCondition]
    unconfirmed: bool  # its research status is weaker than secondary


@dataclass(frozen=True)
class FromHsRow:
    row: HsRate


@dataclass(frozen=True)
class FromBandRow:
    """No HS row matched; the band row's own percentage applies (VAT only)."""

    row: LandedRate


@dataclass(frozen=True)
class FromVendor:
    lookup_id: uuid.UUID
    vendor: str
    answer: StoredAnswer


@dataclass(frozen=True)
class Found:
    rate: Fraction
    source: FromHsRow | FromBandRow | FromVendor
    missed: tuple[MissedPreference, ...]


@dataclass(frozen=True)
class RateNeeded:
    charge: Charge
    reason: Literal["no_hs_code", "no_rate"]


@dataclass(frozen=True)
class RateNotConfirmed:
    """The matching standard rate is weaker than secondary (Q1)."""

    line_no: int


@dataclass(frozen=True)
class AskVendor:
    """The vendor decides and has not been asked about this combination:
    the caller requests a lookup (`duties.lookups.request_duty_rate`)."""

    vendor: str


@dataclass(frozen=True)
class VendorWaiting:
    lookup_id: uuid.UUID


@dataclass(frozen=True)
class VendorUnknownCode:
    lookup_id: uuid.UUID


@dataclass(frozen=True)
class VendorNeedsPerson:
    """The vendor answered, but not in a way the rule may use."""

    lookup_id: uuid.UUID
    reason: Literal["undecodable", "no_charge", "formula", "note"]
    detail: str


type LineRate = (
    Found
    | RateNeeded
    | RateNotConfirmed
    | AskVendor
    | VendorWaiting
    | VendorUnknownCode
    | VendorNeedsPerson
)


def duty_source_for(market: MarketSet, destination: str) -> DutySource:
    """The destination's row in duty_sources.csv; not listed is own_only."""
    for source in market.duty_sources:
        if source.country == destination:
            return source
    return DutySource(destination, DutyMode.OWN_ONLY, None)


def line_rate(
    market: MarketSet,
    destination: str,
    charge: Charge,
    line: Line,
    band_row: LandedRate,
    on: date,
    lookup: VendorLookup | None,
) -> LineRate:
    """The percentage rate for one line under a charge whose band row (from
    the tax path) says it is due. A band row with a flat amount is not
    asked here: the flat amount is the charge. `lookup` is what we hold
    from the destination's vendor for this line's combination, if any."""
    source = duty_source_for(market, destination)
    match source.mode:
        case DutyMode.OWN_ONLY:
            return _own_rate(market, destination, charge, line, band_row, on)
        case DutyMode.OWN_FIRST:
            own = _own_rate(market, destination, charge, line, band_row, on)
            if isinstance(own, Found) or line.hs_code is None:
                return own
            assert source.vendor is not None
            return _vendor_rate(source.vendor, charge, lookup)
        case DutyMode.VENDOR:
            if line.hs_code is None:
                return RateNeeded(charge, "no_hs_code")
            assert source.vendor is not None
            return _vendor_rate(source.vendor, charge, lookup)


def _vendor_rate(vendor: str, charge: Charge, lookup: VendorLookup | None) -> LineRate:
    if lookup is None:
        return AskVendor(vendor)
    assert lookup.vendor == vendor, (lookup.vendor, vendor)
    match lookup.state:
        case LookupState.REQUESTED:
            return VendorWaiting(lookup.id)
        case LookupState.UNKNOWN_CODE:
            return VendorUnknownCode(lookup.id)
        case LookupState.NEEDS_PERSON:
            return VendorNeedsPerson(lookup.id, "undecodable", lookup.detail or "")
        case LookupState.ANSWERED:
            pass
    for answer in lookup.answers:
        if answer.charge != charge:
            continue
        if answer.rate is None:
            return VendorNeedsPerson(lookup.id, "formula", answer.formula)
        if answer.note is not None:
            return VendorNeedsPerson(lookup.id, "note", answer.note)
        return Found(answer.rate, FromVendor(lookup.id, vendor, answer), ())
    return VendorNeedsPerson(lookup.id, "no_charge", charge.value)


def own_standard_row(
    market: MarketSet, destination: str, charge: Charge, line: Line, on: date
) -> HsRate | None:
    """Our standard row for a line (whatever its research status): what a
    vendor's answer is compared with."""
    if line.hs_code is None:
        return None
    return _standard(_matching_rows(market, destination, charge, line, on), line)


def _own_rate(
    market: MarketSet,
    destination: str,
    charge: Charge,
    line: Line,
    band_row: LandedRate,
    on: date,
) -> Found | RateNeeded | RateNotConfirmed:
    hs_rows: list[HsRate] = []
    if line.hs_code is not None:
        hs_rows = _matching_rows(market, destination, charge, line, on)
    standard = _standard(hs_rows, line)
    if standard is None:
        if charge == Charge.IMPORT_TAX and isinstance(band_row.amount, Percent):
            return Found(band_row.amount.rate, FromBandRow(band_row), ())
        if line.hs_code is None:
            return RateNeeded(charge, "no_hs_code")
        return RateNeeded(charge, "no_rate")
    if standard.status not in USABLE_STATUSES:
        return RateNotConfirmed(standard.line_no)
    best = standard
    missed: list[MissedPreference] = []
    for row in hs_rows:
        if row.preference is None or row.origin != line.origin:
            continue
        missing = row.preference.conditions - line.conditions_met
        unconfirmed = row.status not in USABLE_STATUSES
        if missing or unconfirmed:
            missed.append(MissedPreference(row, frozenset(missing), unconfirmed))
        elif row.rate < best.rate:
            best = row
    return Found(best.rate, FromHsRow(best), tuple(missed))


def _matching_rows(
    market: MarketSet, destination: str, charge: Charge, line: Line, on: date
) -> list[HsRate]:
    """Rows for this charge whose prefix starts the HS code and whose origin
    is the line's or any, in force on `on`; the destination's own rows, or
    else its groups'."""
    assert line.hs_code is not None
    places: list[tuple[str | None, str | None]] = [(destination, None)]
    for group in market.groups:
        if destination in group.members:
            places.append((None, group.code))
    for country_code, group_code in places:
        found: list[HsRate] = []
        for row in market.hs_rates:
            if (
                row.scope.country == country_code
                and row.scope.group == group_code
                and row.charge == charge
                and line.hs_code.startswith(row.hs_prefix)
                and row.origin in (None, line.origin)
                and (row.effective_from is None or row.effective_from <= on)
            ):
                found.append(row)
        if found:
            return _latest_per_key(found)
    return []


def _latest_per_key(rows: list[HsRate]) -> list[HsRate]:
    """Of rows that differ only in their start date, the latest started."""
    latest: dict[tuple[object, ...], HsRate] = {}
    for row in rows:
        agreement = row.preference.agreement if row.preference else None
        key = (row.hs_prefix, row.origin, agreement)
        current = latest.get(key)
        if current is None or _started(row) > _started(current):
            latest[key] = row
    kept: list[HsRate] = []
    for row in latest.values():
        kept.append(row)
    return kept


def _started(row: HsRate) -> date:
    return row.effective_from if row.effective_from is not None else date.min


def _standard(rows: list[HsRate], line: Line) -> HsRate | None:
    """The standard rate: the longest prefix, then the line's origin before
    any origin."""
    best: HsRate | None = None
    for row in rows:
        if row.preference is not None:
            continue
        if best is None or _rank(row, line) > _rank(best, line):
            best = row
    return best


def _rank(row: HsRate, line: Line) -> tuple[int, int]:
    return (len(row.hs_prefix), 1 if row.origin == line.origin else 0)
