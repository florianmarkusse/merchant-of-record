"""What a new load changes against the one in force, for a person to read
before it is written (docs/plan-quote.md §2). Pure. Rows are matched by
their natural key; a landed rate by place, category, charge, collector,
band and start date, never by its line in the file, which moves when rows
are inserted above it. A changed row lists only the fields that changed,
written as people read them (9%, 400.00 SGD, 01-Jul-2026)."""

from collections.abc import Callable, Hashable, Sequence
from dataclasses import fields, is_dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction

from mor.dates import format_day
from mor.markets.records import (
    Category,
    Country,
    DutySource,
    FxRate,
    Group,
    HsRate,
    LandedRate,
    Limit,
    MarketSet,
    Percent,
    PerLine,
    PerParcel,
    Preference,
    Registration,
    Scope,
)
from mor.money.amount import Money, format_major


def describe_changes(old: MarketSet | None, new: MarketSet) -> list[str]:
    if old is None:
        return [
            f"first load: {len(new.countries)} countries, {len(new.groups)} groups,"
            f" {len(new.categories)} categories, {len(new.landed_rates)} landed"
            f" rates, {len(new.registrations)} registrations,"
            f" {len(new.fx_rates)} FX rates, {len(new.hs_rates)} HS rates,"
            f" {len(new.duty_sources)} duty sources"
        ]
    lines: list[str] = []
    lines.extend(_table("category", old.categories, new.categories, _category_key))
    lines.extend(_table("country", old.countries, new.countries, _country_key))
    lines.extend(_table("group", old.groups, new.groups, _group_key))
    lines.extend(_table("landed rate", old.landed_rates, new.landed_rates, _landed_key))
    lines.extend(
        _table("registration", old.registrations, new.registrations, _registration_key)
    )
    lines.extend(_table("FX rate", old.fx_rates, new.fx_rates, _fx_key))
    lines.extend(_table("HS rate", old.hs_rates, new.hs_rates, _hs_key))
    lines.extend(
        _table("duty source", old.duty_sources, new.duty_sources, _duty_source_key)
    )
    return lines


def _category_key(row: Category) -> Hashable:
    return row.category


def _country_key(row: Country) -> Hashable:
    return row.iso2


def _group_key(row: Group) -> Hashable:
    return row.code


def _registration_key(row: Registration) -> Hashable:
    return f"{row.country} {row.scheme}"


def _fx_key(row: FxRate) -> Hashable:
    return f"{row.base.value}/{row.quote.value}"


def _hs_key(rate: HsRate) -> Hashable:
    place = rate.scope.country or rate.scope.group
    origin = rate.origin or "any origin"
    agreement = rate.preference.agreement if rate.preference else "standard"
    started = ""
    if rate.effective_from is not None:
        started = f" from {format_day(rate.effective_from)}"
    return (
        f"{place}, HS {rate.hs_prefix}, {rate.charge.value}, {origin},"
        f" {agreement}{started}"
    )


def _duty_source_key(row: DutySource) -> Hashable:
    return row.country


def _landed_key(rate: LandedRate) -> Hashable:
    place = rate.scope.country or rate.scope.group
    category = rate.category or "every category"
    started = ""
    if rate.effective_from is not None:
        started = f" from {format_day(rate.effective_from)}"
    return (
        f"{place}, {category}, {rate.charge.value}, {rate.collector.value},"
        f" {rate.band.value}{started}"
    )


# Fields that say where a row sits, not what it says: a move is no change.
_IGNORED = frozenset({"line_no"})


def _table[R](
    name: str, old: Sequence[R], new: Sequence[R], key: Callable[[R], Hashable]
) -> list[str]:
    before: dict[Hashable, R] = {}
    for row in old:
        before[key(row)] = row
    after: dict[Hashable, R] = {}
    for row in new:
        after[key(row)] = row
    lines: list[str] = []
    for row_key, row in after.items():
        if row_key not in before:
            lines.append(f"+ {name} {row_key}: {_record_text(row)}")
            continue
        changed = _changed_fields(before[row_key], row)
        if changed:
            lines.append(f"~ {name} {row_key}: {'; '.join(changed)}")
    for row_key, row in before.items():
        if row_key not in after:
            lines.append(f"- {name} {row_key}: {_record_text(row)}")
    return lines


def _changed_fields(old: object, new: object) -> list[str]:
    changed: list[str] = []
    if not is_dataclass(old) or not is_dataclass(new):
        return changed
    for field in fields(old):
        if field.name in _IGNORED:
            continue
        if getattr(old, field.name) != getattr(new, field.name):
            before = _field_text(old, field.name)
            after = _field_text(new, field.name)
            changed.append(f"{field.name} {before} -> {after}")
    return changed


def _record_text(row: object) -> str:
    parts: list[str] = []
    if not is_dataclass(row):
        return value_text(row)
    for field in fields(row):
        if field.name in _IGNORED:
            continue
        parts.append(f"{field.name} {_field_text(row, field.name)}")
    return "; ".join(parts)


def _field_text(row: object, name: str) -> str:
    if isinstance(row, Group) and name == "members":
        return " ".join(row.members)
    if isinstance(row, HsRate) and name == "rate":
        return _percent(row.rate)
    return value_text(getattr(row, name))


def value_text(value: object) -> str:
    """A field's value as a person reads it."""
    match value:
        case None:
            return "none"
        case StrEnum():
            return value.value
        case Money():
            return f"{format_major(value)} {value.currency.value}"
        case Fraction():
            return _decimal(value)
        case date():
            return format_day(value)
        case Limit(amount=amount, basis=basis):
            return f"{value_text(amount)} on {basis.value}"
        case Percent(rate=rate, base=base):
            return f"{_percent(rate)} of {base.value}"
        case PerLine(amount=amount):
            return f"{value_text(amount)} per line"
        case PerParcel(amount=amount):
            return f"{value_text(amount)} per parcel"
        case Preference(agreement=agreement, conditions=conditions):
            needs: list[str] = []
            for condition in conditions:
                needs.append(condition.value)
            if not needs:
                return agreement
            return f"{agreement} (needs {', '.join(sorted(needs))})"
        case Scope(country=country, group=group):
            return country or group or "none"
        case _:
            return str(value)


def _percent(rate: Fraction) -> str:
    """12.5% from Fraction(1, 8); exact rates that are not a short decimal
    are written as a fraction of a percent."""
    percent = rate * 100
    if percent.denominator == 1:
        return f"{percent.numerator}%"
    for places in range(1, 5):
        scaled = percent * 10**places
        if scaled.denominator == 1:
            whole, rest = divmod(scaled.numerator, 10**places)
            return f"{whole}.{rest:0{places}d}%"
    return f"{percent.numerator}/{percent.denominator}%"


def _decimal(value: Fraction) -> str:
    """25600 or 1.4309 when the fraction has a short decimal; else n/d."""
    if value.denominator == 1:
        return str(value.numerator)
    for places in range(1, 9):
        scaled = value * 10**places
        if scaled.denominator == 1:
            whole, rest = divmod(scaled.numerator, 10**places)
            return f"{whole}.{rest:0{places}d}"
    return f"{value.numerator}/{value.denominator}"
