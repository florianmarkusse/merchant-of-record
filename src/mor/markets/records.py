"""Markets as plain records: one complete set per load (docs/plan-quote.md
§2). The enums match the PostgreSQL enum types of `0008_markets.sql`.
Categories are strings that must name a row of the set's categories: they
grow with the data, so they are not an enum."""

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction

from mor.money.amount import Money
from mor.money.currency import Currency


class MarketStatus(StrEnum):
    OPEN = "open"
    CONDITIONAL = "conditional"
    CLOSED = "closed"
    UNDECIDED = "undecided"
    NOT_ASSESSED = "not_assessed"


class ResearchStatus(StrEnum):
    VERIFIED = "verified"
    SECONDARY = "secondary"
    ESTIMATE = "estimate"
    UNVERIFIED = "unverified"
    TO_FETCH = "to_fetch"
    AWAITING_ANSWER = "awaiting_answer"
    NOT_APPLICABLE = "n/a"


# What a quote may rely on (decided 17-Nov-2026, docs/plan-quote.md Q1).
USABLE_STATUSES = frozenset({ResearchStatus.VERIFIED, ResearchStatus.SECONDARY})


class GroupKind(StrEnum):
    LEGAL = "legal"
    REGION = "region"


class Charge(StrEnum):
    DUTY = "duty"
    ADDITIONAL_DUTY = "additional_duty"
    FEE = "fee"
    IMPORT_TAX = "import_tax"
    OTHER_TAX = "other_tax"


class Collector(StrEnum):
    BORDER = "border"
    SELLER = "seller"


class Band(StrEnum):
    ALWAYS = "always"
    UP_TO = "up_to"
    ABOVE = "above"


class LimitBasis(StrEnum):
    GOODS = "goods"
    GOODS_PLUS_FREIGHT = "goods_plus_freight"


class Base(StrEnum):
    GOODS = "goods"
    GOODS_PLUS_FREIGHT = "goods_plus_freight"
    GOODS_FREIGHT_DUTY = "goods_freight_duty"


class PreferenceCondition(StrEnum):
    """What a trade agreement's preferential rate requires (decided
    18-Nov-2026: a closed list, not free text). A new kind needs code to
    check it, so it is an enum."""

    ORIGIN_STATEMENT = "origin_statement"
    CERTIFICATE_OF_ORIGIN = "certificate_of_origin"
    EXPORTER_REGISTRATION = "exporter_registration"
    DIRECT_SHIPMENT = "direct_shipment"
    NOT_UNDER_IOSS = "not_under_ioss"


class DutySourceKind(StrEnum):
    """Where a destination's duty rates come from."""

    OWN_TABLE = "own_table"
    VENDOR = "vendor"


@dataclass(frozen=True)
class Category:
    category: str
    label: str


@dataclass(frozen=True)
class Country:
    iso2: str
    name: str
    currency: str | None
    status: MarketStatus
    status_note: str


@dataclass(frozen=True)
class Group:
    code: str
    label: str
    kind: GroupKind
    members: tuple[str, ...]


@dataclass(frozen=True)
class Limit:
    """The band's edge: the order's value on `basis`, in its currency."""

    amount: Money
    basis: LimitBasis


@dataclass(frozen=True)
class Percent:
    """A share of a base, exact: 12.5% is Fraction(1, 8)."""

    rate: Fraction
    base: Base


@dataclass(frozen=True)
class PerLine:
    amount: Money


@dataclass(frozen=True)
class PerParcel:
    amount: Money


type Amount = Percent | PerLine | PerParcel


@dataclass(frozen=True)
class Scope:
    """Exactly one of a country or a group."""

    country: str | None
    group: str | None


@dataclass(frozen=True)
class LandedRate:
    line_no: int  # the row in landed_rates.csv, 2 for the first
    scope: Scope
    category: str | None  # None: every category
    charge: Charge
    collector: Collector
    band: Band
    limit: Limit | None  # None exactly when the band is `always`
    amount: Amount
    effective_from: date | None  # None: in force before our records
    status: ResearchStatus
    source: str
    source_url: str


@dataclass(frozen=True)
class Preference:
    """A trade agreement's rate: applies only when every condition is met."""

    agreement: str
    conditions: frozenset[PreferenceCondition]


@dataclass(frozen=True)
class HsRate:
    """A rate for products whose HS code starts with `hs_prefix`, into one
    destination (or group), from one origin (None: any)."""

    line_no: int  # the row in hs_rates.csv, 2 for the first
    scope: Scope
    hs_prefix: str  # digits only, 2 to 10
    charge: Charge
    origin: str | None
    preference: Preference | None  # None: the standard rate
    rate: Fraction
    effective_from: date | None
    status: ResearchStatus
    source: str
    source_url: str


@dataclass(frozen=True)
class DutySource:
    country: str
    kind: DutySourceKind


@dataclass(frozen=True)
class Registration:
    country: str
    scheme: str
    number: str
    effective_from: date


@dataclass(frozen=True)
class FxRate:
    """Units of `quote` per one unit of `base`, exact."""

    base: Currency
    quote: Currency
    rate: Fraction
    as_of: date
    status: ResearchStatus
    source: str


@dataclass(frozen=True)
class MarketSet:
    """Everything one load holds. Tuples in file order."""

    categories: tuple[Category, ...]
    countries: tuple[Country, ...]
    groups: tuple[Group, ...]
    landed_rates: tuple[LandedRate, ...]
    registrations: tuple[Registration, ...]
    fx_rates: tuple[FxRate, ...]
    hs_rates: tuple[HsRate, ...]
    duty_sources: tuple[DutySource, ...]  # a destination not listed: own_table


def _canonical_lines(market: MarketSet) -> list[str]:
    """One line per record, every field spelled out: what the content hash
    covers. Notes and research references are not here, so editing them
    alone makes no new load."""
    lines: list[str] = []
    for category in market.categories:
        lines.append(f"category|{category.category}|{category.label}")
    for country in market.countries:
        lines.append(
            f"country|{country.iso2}|{country.name}|{country.currency}"
            f"|{country.status.value}|{country.status_note}"
        )
    for group in market.groups:
        members = " ".join(group.members)
        lines.append(f"group|{group.code}|{group.label}|{group.kind.value}|{members}")
    for rate in market.landed_rates:
        lines.append(
            f"landed|{rate.line_no}|{rate.scope.country}|{rate.scope.group}"
            f"|{rate.category}|{rate.charge.value}|{rate.collector.value}"
            f"|{rate.band.value}|{_limit_text(rate.limit)}|{_amount_text(rate.amount)}"
            f"|{rate.effective_from}|{rate.status.value}|{rate.source}|{rate.source_url}"
        )
    for registration in market.registrations:
        lines.append(
            f"registration|{registration.country}|{registration.scheme}"
            f"|{registration.number}|{registration.effective_from}"
        )
    for fx in market.fx_rates:
        lines.append(
            f"fx|{fx.base}|{fx.quote}|{fx.rate.numerator}/{fx.rate.denominator}"
            f"|{fx.as_of}|{fx.status.value}|{fx.source}"
        )
    for hs in market.hs_rates:
        preference = "-"
        if hs.preference is not None:
            conditions: list[str] = []
            for condition in hs.preference.conditions:
                conditions.append(condition.value)
            preference = f"{hs.preference.agreement} {' '.join(sorted(conditions))}"
        lines.append(
            f"hs|{hs.line_no}|{hs.scope.country}|{hs.scope.group}|{hs.hs_prefix}"
            f"|{hs.charge.value}|{hs.origin}|{preference}"
            f"|{hs.rate.numerator}/{hs.rate.denominator}|{hs.effective_from}"
            f"|{hs.status.value}|{hs.source}|{hs.source_url}"
        )
    for duty_source in market.duty_sources:
        lines.append(f"duty_source|{duty_source.country}|{duty_source.kind.value}")
    return lines


def _limit_text(limit: Limit | None) -> str:
    if limit is None:
        return "-"
    money = limit.amount
    return f"{money.amount_minor} {money.currency.value} {limit.basis.value}"


def _amount_text(amount: Amount) -> str:
    match amount:
        case Percent(rate=rate, base=base):
            return f"percent {rate.numerator}/{rate.denominator} {base.value}"
        case PerLine(amount=money):
            return f"per_line {money.amount_minor} {money.currency.value}"
        case PerParcel(amount=money):
            return f"per_parcel {money.amount_minor} {money.currency.value}"


def content_sha256(market: MarketSet) -> str:
    text = "\n".join(_canonical_lines(market))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
