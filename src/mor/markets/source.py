"""Read `docs/markets/` into a `MarketSet` (docs/plan-quote.md §2). Every
value is parsed and every reference checked here, so a load either gets a
complete, consistent set or a list of errors naming file, line and
problem. Nothing is guessed: a value that does not parse is an error.

The research script (`scripts/markets.py check`) checks the same files for
people; this is the check for what quoting relies on.
"""

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction
from pathlib import Path

from mor.markets.records import (
    Amount,
    Band,
    Base,
    Category,
    Charge,
    Collector,
    Country,
    DutyMode,
    DutySource,
    FxRate,
    Group,
    GroupKind,
    HsRate,
    LandedRate,
    Limit,
    LimitBasis,
    MarketSet,
    MarketStatus,
    Percent,
    PerLine,
    PerParcel,
    Preference,
    PreferenceCondition,
    Registration,
    ResearchStatus,
    Scope,
)
from mor.money.amount import Money
from mor.money.currency import Currency


FILES = (
    "categories",
    "countries",
    "groups",
    "landed_rates",
    "registrations",
    "fx_rates",
    "hs_rates",
    "duty_sources",
)

_ISO2 = re.compile(r"[A-Z]{2}")
_INTEGER = re.compile(r"[0-9]+")
_DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")
_VENDOR_NAME = re.compile(r"[a-z][a-z0-9_]*")
_HS_PREFIX = re.compile(r"[0-9]{2,10}")

type Row = Mapping[str, str]


@dataclass(frozen=True)
class SourceError:
    file: str
    line_no: int  # 1 is the header; 0 is the file as a whole
    problem: str

    def __str__(self) -> str:
        return f"{self.file}.csv line {self.line_no}: {self.problem}"


@dataclass(frozen=True)
class At:
    """Where a field is read: its file, line and row."""

    file: str
    line_no: int
    row: Row


@dataclass(frozen=True)
class Unparsed:
    """A field that did not parse; its error is already recorded."""


def read_texts(directory: Path) -> dict[str, str] | list[SourceError]:
    texts: dict[str, str] = {}
    errors: list[SourceError] = []
    for name in FILES:
        path = directory / f"{name}.csv"
        try:
            texts[name] = path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(SourceError(name, 0, f"cannot read: {error.strerror}"))
    if errors:
        return errors
    return texts


def parse(texts: Mapping[str, str]) -> MarketSet | list[SourceError]:
    errors: list[SourceError] = []
    countries = _countries(texts["countries"], errors)
    country_codes: set[str] = set()
    for country in countries:
        country_codes.add(country.iso2)
    market = MarketSet(
        tuple(_categories(texts["categories"], errors)),
        tuple(countries),
        tuple(_groups(texts["groups"], errors)),
        tuple(_landed_rates(texts["landed_rates"], country_codes, errors)),
        tuple(_registrations(texts["registrations"], errors)),
        tuple(_fx_rates(texts["fx_rates"], errors)),
        tuple(_hs_rates(texts["hs_rates"], country_codes, errors)),
        tuple(_duty_sources(texts["duty_sources"], errors)),
    )
    _check_references(market, errors)
    if errors:
        return errors
    return market


# --- fields -------------------------------------------------------------------


def _rows(file: str, text: str, errors: list[SourceError]) -> list[At]:
    rows: list[At] = []
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        for line_no, raw in enumerate(reader, start=2):
            row: dict[str, str] = {}
            for key, value in raw.items():
                if isinstance(key, str) and isinstance(value, str):
                    row[key] = value.strip()
            rows.append(At(file, line_no, row))
    except csv.Error as error:
        errors.append(SourceError(file, 0, f"not CSV: {error}"))
    return rows


def _fail(errors: list[SourceError], at: At, problem: str) -> None:
    errors.append(SourceError(at.file, at.line_no, problem))


def _text(errors: list[SourceError], at: At, column: str) -> str | Unparsed:
    value = at.row.get(column)
    if value is None:
        _fail(errors, at, f"no column {column!r}")
        return Unparsed()
    return value


def _required(errors: list[SourceError], at: At, column: str) -> str | Unparsed:
    value = _text(errors, at, column)
    if value == "":
        _fail(errors, at, f"{column} is empty")
        return Unparsed()
    return value


def _iso2(errors: list[SourceError], at: At, column: str) -> str | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, str) and not _ISO2.fullmatch(value):
        _fail(errors, at, f"{column} {value!r} is not an ISO 3166 code")
        return Unparsed()
    return value


def _currency(errors: list[SourceError], at: At, column: str) -> Currency | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, Unparsed):
        return value
    for currency in Currency:
        if currency.value == value:
            return currency
    _fail(errors, at, f"{column} {value!r} is not a currency we support")
    return Unparsed()


def _minor(errors: list[SourceError], at: At, column: str) -> int | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, Unparsed):
        return value
    if not _INTEGER.fullmatch(value):
        _fail(errors, at, f"{column} {value!r} is not a whole number")
        return Unparsed()
    return int(value)


def _decimal(errors: list[SourceError], at: At, column: str) -> Fraction | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, Unparsed):
        return value
    if not _DECIMAL.fullmatch(value):
        _fail(errors, at, f"{column} {value!r} is not a decimal number")
        return Unparsed()
    return Fraction(value)


def _day(errors: list[SourceError], at: At, column: str) -> date | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, Unparsed):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(errors, at, f"{column} {value!r} is not a date (YYYY-MM-DD)")
        return Unparsed()


def _choice[E: StrEnum](
    errors: list[SourceError], at: At, column: str, options: type[E]
) -> E | Unparsed:
    value = _required(errors, at, column)
    if isinstance(value, Unparsed):
        return value
    for option in options:
        if option.value == value:
            return option
    allowed = ", ".join(option.value for option in options)
    _fail(errors, at, f"{column} {value!r} is not one of: {allowed}")
    return Unparsed()


# --- files --------------------------------------------------------------------


def _categories(text: str, errors: list[SourceError]) -> list[Category]:
    parsed: list[Category] = []
    for at in _rows("categories", text, errors):
        category = _required(errors, at, "category")
        label = _required(errors, at, "label")
        if isinstance(category, str) and isinstance(label, str):
            parsed.append(Category(category, label))
    return parsed


def _countries(text: str, errors: list[SourceError]) -> list[Country]:
    parsed: list[Country] = []
    for at in _rows("countries", text, errors):
        iso2 = _iso2(errors, at, "iso2")
        name = _required(errors, at, "name")
        currency = _text(errors, at, "currency")
        status = _choice(errors, at, "market_status", MarketStatus)
        note = _text(errors, at, "status_note")
        if (
            isinstance(iso2, str)
            and isinstance(name, str)
            and isinstance(currency, str)
            and isinstance(status, MarketStatus)
            and isinstance(note, str)
        ):
            parsed.append(Country(iso2, name, currency or None, status, note))
    return parsed


def _groups(text: str, errors: list[SourceError]) -> list[Group]:
    parsed: list[Group] = []
    for at in _rows("groups", text, errors):
        code = _required(errors, at, "group")
        label = _required(errors, at, "label")
        kind = _choice(errors, at, "kind", GroupKind)
        members = _text(errors, at, "members")
        if (
            isinstance(code, str)
            and isinstance(label, str)
            and isinstance(kind, GroupKind)
            and isinstance(members, str)
        ):
            # Member order means nothing; sorted, as the database returns it.
            parsed.append(Group(code, label, kind, tuple(sorted(members.split()))))
    return parsed


def _landed_rates(
    text: str, country_codes: set[str], errors: list[SourceError]
) -> list[LandedRate]:
    """A row's `country` is a country if the countries file lists it, else a
    group (EU looks like a country code, but is a group)."""
    parsed: list[LandedRate] = []
    for at in _rows("landed_rates", text, errors):
        place = _required(errors, at, "country")
        category = _text(errors, at, "category")
        charge = _choice(errors, at, "charge", Charge)
        collector = _choice(errors, at, "collected", Collector)
        band = _choice(errors, at, "applies", Band)
        limit = _limit(errors, at, band)
        amount = _amount(errors, at)
        effective_from: date | None | Unparsed = None
        if at.row.get("effective_from", ""):
            effective_from = _day(errors, at, "effective_from")
        status = _choice(errors, at, "status", ResearchStatus)
        source = _text(errors, at, "source")
        source_url = _text(errors, at, "source_url")
        if (
            isinstance(place, Unparsed)
            or isinstance(category, Unparsed)
            or isinstance(charge, Unparsed)
            or isinstance(collector, Unparsed)
            or isinstance(band, Unparsed)
            or isinstance(limit, Unparsed)
            or isinstance(amount, Unparsed)
            or isinstance(effective_from, Unparsed)
            or isinstance(status, Unparsed)
            or isinstance(source, Unparsed)
            or isinstance(source_url, Unparsed)
        ):
            continue
        if place in country_codes:
            scope = Scope(place, None)
        else:
            scope = Scope(None, place)
        parsed.append(
            LandedRate(
                at.line_no,
                scope,
                category or None,
                charge,
                collector,
                band,
                limit,
                amount,
                effective_from,
                status,
                source,
                source_url,
            )
        )
    return parsed


def _limit(
    errors: list[SourceError], at: At, band: Band | Unparsed
) -> Limit | None | Unparsed:
    """The band's limit, or None for an `always` band."""
    if isinstance(band, Unparsed):
        return band
    if band == Band.ALWAYS:
        for column in ("limit_minor", "limit_currency", "limit_basis"):
            if at.row.get(column, ""):
                _fail(errors, at, f"{column} is set on an `always` band")
                return Unparsed()
        return None
    amount = _minor(errors, at, "limit_minor")
    currency = _currency(errors, at, "limit_currency")
    basis = _choice(errors, at, "limit_basis", LimitBasis)
    if (
        isinstance(amount, Unparsed)
        or isinstance(currency, Unparsed)
        or isinstance(basis, Unparsed)
    ):
        return Unparsed()
    return Limit(Money(amount, currency), basis)


def _amount(errors: list[SourceError], at: At) -> Amount | Unparsed:
    basis = at.row.get("basis", "")
    match basis:
        case "percent":
            rate = _decimal(errors, at, "rate_percent")
            base = _choice(errors, at, "base", Base)
            if isinstance(rate, Unparsed) or isinstance(base, Unparsed):
                return Unparsed()
            return Percent(rate / 100, base)
        case "per_line" | "per_parcel":
            minor = _minor(errors, at, "amount_minor")
            currency = _currency(errors, at, "currency")
            if isinstance(minor, Unparsed) or isinstance(currency, Unparsed):
                return Unparsed()
            if basis == "per_line":
                return PerLine(Money(minor, currency))
            return PerParcel(Money(minor, currency))
        case _:
            _fail(
                errors,
                at,
                f"basis {basis!r} is not one of: percent, per_line, per_parcel",
            )
            return Unparsed()


def _registrations(text: str, errors: list[SourceError]) -> list[Registration]:
    parsed: list[Registration] = []
    for at in _rows("registrations", text, errors):
        country = _iso2(errors, at, "country")
        scheme = _required(errors, at, "scheme")
        number = _required(errors, at, "number")
        effective_from = _day(errors, at, "effective_from")
        if (
            isinstance(country, str)
            and isinstance(scheme, str)
            and isinstance(number, str)
            and isinstance(effective_from, date)
        ):
            parsed.append(Registration(country, scheme, number, effective_from))
    return parsed


def _fx_rates(text: str, errors: list[SourceError]) -> list[FxRate]:
    parsed: list[FxRate] = []
    for at in _rows("fx_rates", text, errors):
        base = _currency(errors, at, "base")
        quote = _currency(errors, at, "quote")
        rate = _decimal(errors, at, "rate")
        as_of = _day(errors, at, "as_of")
        status = _choice(errors, at, "status", ResearchStatus)
        source = _required(errors, at, "source")
        if isinstance(rate, Fraction) and rate == 0:
            _fail(errors, at, "rate is zero")
            continue
        if (
            isinstance(base, Currency)
            and isinstance(quote, Currency)
            and isinstance(rate, Fraction)
            and isinstance(as_of, date)
            and isinstance(status, ResearchStatus)
            and isinstance(source, str)
        ):
            parsed.append(FxRate(base, quote, rate, as_of, status, source))
    return parsed


def _hs_rates(
    text: str, country_codes: set[str], errors: list[SourceError]
) -> list[HsRate]:
    parsed: list[HsRate] = []
    for at in _rows("hs_rates", text, errors):
        place = _required(errors, at, "country")
        prefix = _required(errors, at, "hs_prefix")
        if isinstance(prefix, str) and not _HS_PREFIX.fullmatch(prefix):
            _fail(errors, at, f"hs_prefix {prefix!r} is not 2 to 10 digits")
            prefix = Unparsed()
        charge = _choice(errors, at, "charge", Charge)
        origin: str | None | Unparsed = None
        if at.row.get("origin", ""):
            origin = _iso2(errors, at, "origin")
        preference = _preference(errors, at, origin)
        rate = _decimal(errors, at, "rate_percent")
        effective_from: date | None | Unparsed = None
        if at.row.get("effective_from", ""):
            effective_from = _day(errors, at, "effective_from")
        status = _choice(errors, at, "status", ResearchStatus)
        source = _text(errors, at, "source")
        source_url = _text(errors, at, "source_url")
        if (
            isinstance(place, Unparsed)
            or isinstance(prefix, Unparsed)
            or isinstance(charge, Unparsed)
            or isinstance(origin, Unparsed)
            or isinstance(preference, Unparsed)
            or isinstance(rate, Unparsed)
            or isinstance(effective_from, Unparsed)
            or isinstance(status, Unparsed)
            or isinstance(source, Unparsed)
            or isinstance(source_url, Unparsed)
        ):
            continue
        if place in country_codes:
            scope = Scope(place, None)
        else:
            scope = Scope(None, place)
        parsed.append(
            HsRate(
                at.line_no,
                scope,
                prefix,
                charge,
                origin,
                preference,
                rate / 100,
                effective_from,
                status,
                source,
                source_url,
            )
        )
    return parsed


def _preference(
    errors: list[SourceError], at: At, origin: str | None | Unparsed
) -> Preference | None | Unparsed:
    """A trade agreement's rate: an agreement, the origin it is for, and
    conditions from the closed list. No agreement: the standard rate."""
    agreement = at.row.get("agreement", "")
    words = at.row.get("conditions", "").split()
    if not agreement:
        if words:
            _fail(errors, at, "conditions without an agreement")
            return Unparsed()
        return None
    if origin is None:
        _fail(errors, at, f"agreement {agreement} without the origin it is for")
        return Unparsed()
    conditions: set[PreferenceCondition] = set()
    for word in words:
        found: PreferenceCondition | None = None
        for condition in PreferenceCondition:
            if condition.value == word:
                found = condition
        if found is None:
            allowed = ", ".join(condition.value for condition in PreferenceCondition)
            _fail(errors, at, f"condition {word!r} is not one of: {allowed}")
            return Unparsed()
        conditions.add(found)
    return Preference(agreement, frozenset(conditions))


def _duty_sources(text: str, errors: list[SourceError]) -> list[DutySource]:
    parsed: list[DutySource] = []
    for at in _rows("duty_sources", text, errors):
        country = _iso2(errors, at, "country")
        mode = _choice(errors, at, "mode", DutyMode)
        vendor = _text(errors, at, "vendor")
        if isinstance(mode, Unparsed) or isinstance(vendor, Unparsed):
            continue
        if mode == DutyMode.OWN_ONLY and vendor != "":
            _fail(errors, at, "own_only names no vendor")
            continue
        if mode != DutyMode.OWN_ONLY and not _VENDOR_NAME.fullmatch(vendor):
            _fail(errors, at, f"{mode.value} needs a vendor name (a-z, 0-9, _)")
            continue
        if isinstance(country, str):
            parsed.append(DutySource(country, mode, vendor or None))
    return parsed


# --- references -----------------------------------------------------------------


def _check_references(market: MarketSet, errors: list[SourceError]) -> None:
    """Every name a row uses exists in the set, and keys are unique."""
    categories: set[str] = set()
    for category in market.categories:
        if category.category in categories:
            errors.append(SourceError("categories", 0, f"{category.category} twice"))
        categories.add(category.category)
    countries: set[str] = set()
    for country in market.countries:
        if country.iso2 in countries:
            errors.append(SourceError("countries", 0, f"{country.iso2} twice"))
        countries.add(country.iso2)
    groups: set[str] = set()
    for group in market.groups:
        if group.code in groups or group.code in countries:
            errors.append(SourceError("groups", 0, f"{group.code} twice"))
        groups.add(group.code)
        for member in group.members:
            if member not in countries:
                problem = f"{group.code} lists unknown country {member}"
                errors.append(SourceError("groups", 0, problem))
    for rate in market.landed_rates:
        country, group = rate.scope.country, rate.scope.group
        if country is not None and country not in countries:
            problem = f"unknown country {country}"
            errors.append(SourceError("landed_rates", rate.line_no, problem))
        if group is not None and group not in groups:
            problem = f"{group} is neither a country code nor a group"
            errors.append(SourceError("landed_rates", rate.line_no, problem))
        if rate.category is not None and rate.category not in categories:
            problem = f"unknown category {rate.category}"
            errors.append(SourceError("landed_rates", rate.line_no, problem))
    registered: set[tuple[str, str]] = set()
    for registration in market.registrations:
        if registration.country not in countries:
            problem = f"unknown country {registration.country}"
            errors.append(SourceError("registrations", 0, problem))
        key = (registration.country, registration.scheme)
        if key in registered:
            problem = f"{registration.country} {registration.scheme} twice"
            errors.append(SourceError("registrations", 0, problem))
        registered.add(key)
    groups_and_countries = groups | countries
    for hs in market.hs_rates:
        place = hs.scope.country or hs.scope.group or ""
        if place not in groups_and_countries:
            problem = f"{place} is neither a country code nor a group"
            errors.append(SourceError("hs_rates", hs.line_no, problem))
        if hs.origin is not None and hs.origin not in countries:
            problem = f"unknown origin {hs.origin}"
            errors.append(SourceError("hs_rates", hs.line_no, problem))
    sourced: set[str] = set()
    for duty_source in market.duty_sources:
        if duty_source.country not in countries:
            problem = f"unknown country {duty_source.country}"
            errors.append(SourceError("duty_sources", 0, problem))
        if duty_source.country in sourced:
            problem = f"{duty_source.country} twice"
            errors.append(SourceError("duty_sources", 0, problem))
        sourced.add(duty_source.country)
    pairs: set[tuple[Currency, Currency]] = set()
    for fx in market.fx_rates:
        if (fx.base, fx.quote) in pairs:
            errors.append(SourceError("fx_rates", 0, f"{fx.base}/{fx.quote} twice"))
        pairs.add((fx.base, fx.quote))
