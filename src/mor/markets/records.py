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


class DutyMode(StrEnum):
    """Who decides a country's rates per HS code (duty and import tax);
    matches `duty_mode` (docs/plan-quote.md §3, 4b-2)."""

    VENDOR = "vendor"  # the vendor's answer; our rows are compared with it
    OWN_FIRST = "own_first"  # our rows; the vendor fills the gaps
    OWN_ONLY = "own_only"  # our rows; none refuses the quote


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
    mode: DutyMode
    vendor: str | None  # the vendor's name (`zonos`); None exactly for own_only


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


# --- shipping and fees (4c, docs/plan-quote.md §4) ------------------------------


class ServiceTier(StrEnum):
    EXPRESS = "express"
    DAY_DEFINITE = "day_definite"
    ECONOMY = "economy"
    POSTAL = "postal"


class DutiesPaid(StrEnum):
    """Whether the carrier can pay duties and taxes and bill us (DDP)."""

    YES = "yes"
    MANDATORY = "mandatory"
    IOSS_ONLY = "ioss_only"  # only into the EU (EU VAT and the flat duty)
    ON_REQUEST = "on_request"
    NO = "no"
    UNKNOWN = "unknown"


class CardFuel(StrEnum):
    EXTRA = "extra"  # a fuel surcharge row adds it
    INCLUDED = "included"
    UNKNOWN = "unknown"


class SurchargeBasis(StrEnum):
    PERCENT_OF_FREIGHT = "percent_of_freight"
    PERCENT_OF_DUTIES = "percent_of_duties"
    PERCENT_OF_VALUE = "percent_of_value"
    PER_SHIPMENT = "per_shipment"
    PER_PIECE = "per_piece"
    PER_KG = "per_kg"
    PER_LINE = "per_line"


class SurchargeTrigger(StrEnum):
    """When a surcharge is charged. A quote adds `always`, and
    `duties_paid` when something is paid at the border; the others are
    not known at checkout (decided 18-Nov-2026, plan-quote §4)."""

    ALWAYS = "always"
    DUTIES_PAID = "duties_paid"
    PASSTHROUGH = "passthrough"
    OPTIONAL = "optional"
    ON_EVENT = "on_event"


class FuelApplies(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class RestrictionRule(StrEnum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class PriceSource(StrEnum):
    """Where a route's shipping price comes from: our rate card, or the
    carrier's live rate call (4c-2)."""

    OWN_TABLE = "own_table"
    CARRIER_API = "carrier_api"


class Standing(StrEnum):
    """`placeholder`: a stand-in until a contract or a decision exists; prod
    refuses to quote on one (decided 17-Nov-2026, plan-quote Q5)."""

    PLACEHOLDER = "placeholder"
    AGREED = "agreed"


class FeeComponent(StrEnum):
    """The named parts of our fee (plan-architecture §8)."""

    PSP_FEE = "psp_fee"
    FX = "fx"
    MOR_MARGIN = "mor_margin"


@dataclass(frozen=True)
class Service:
    code: str
    provider: str
    name: str
    origin: str
    tier: ServiceTier
    duties_paid: DutiesPaid
    volumetric_divisor: int | None  # cm³ per kg
    max_weight_kg: Fraction | None
    status: ResearchStatus


@dataclass(frozen=True)
class RateCard:
    code: str
    service: str
    currency: Currency
    effective_from: date | None
    effective_to: date | None
    fuel: CardFuel
    status: ResearchStatus
    source: str


@dataclass(frozen=True)
class CardZone:
    rate_card: str
    destination: str  # a country
    zone: str
    status: ResearchStatus


@dataclass(frozen=True)
class CardRate:
    """The price of one weight break in one zone, in the card's currency.
    With a maximum, the card gives a range: an estimate, not a price."""

    rate_card: str
    zone: str
    weight_kg: Fraction
    amount_minor: int
    amount_max_minor: int | None


@dataclass(frozen=True)
class Surcharge:
    line_no: int  # the row in surcharges.csv
    service: str
    rate_card: str | None  # None: every card of the service
    name: str
    basis: SurchargeBasis
    rate: Fraction | None  # for the percent bases, exact
    amount_minor: int | None
    min_amount_minor: int | None
    currency: Currency
    trigger: SurchargeTrigger
    destinations: tuple[str, ...]  # countries or groups; empty: everywhere
    fuel_applies: FuelApplies
    valid_until: date | None  # a weekly fuel rate's last day
    status: ResearchStatus
    source: str


@dataclass(frozen=True)
class Transit:
    service: str
    scope: Scope
    days_min: int
    days_max: int
    status: ResearchStatus


@dataclass(frozen=True)
class ServiceRestriction:
    service: str
    category: str
    rule: RestrictionRule
    status: ResearchStatus


@dataclass(frozen=True)
class ShippingRoute:
    """A service offered into a country or group, priced from a rate card
    (`own_table`) or by the carrier (`carrier_api`, no card)."""

    scope: Scope
    service: str
    rate_card: str | None  # set exactly when the source is own_table
    source: PriceSource
    standing: Standing

    @property
    def key(self) -> str:
        """What names the option it gives: the card, or the live service."""
        if self.rate_card is not None:
            return self.rate_card
        return f"live:{self.service}"


@dataclass(frozen=True)
class FeeRule:
    """One fee component for buyers in a country or group (None: the
    default), from a date. A percentage of the merchant's price plus an
    optional fixed amount (decided 18-Nov-2026, plan-quote §4)."""

    line_no: int  # the row in fees.csv
    component: FeeComponent
    scope: Scope | None
    rate: Fraction
    fixed: Money | None
    effective_from: date
    standing: Standing
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
    duty_sources: tuple[DutySource, ...]  # a destination not listed: own_only
    services: tuple[Service, ...]
    rate_cards: tuple[RateCard, ...]
    zones: tuple[CardZone, ...]
    rates: tuple[CardRate, ...]
    surcharges: tuple[Surcharge, ...]
    transit: tuple[Transit, ...]
    restrictions: tuple[ServiceRestriction, ...]
    routes: tuple[ShippingRoute, ...]
    fees: tuple[FeeRule, ...]


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
        lines.append(
            f"duty_source|{duty_source.country}|{duty_source.mode.value}"
            f"|{duty_source.vendor}"
        )
    for service in market.services:
        lines.append(
            f"service|{service.code}|{service.provider}|{service.name}"
            f"|{service.origin}|{service.tier.value}|{service.duties_paid.value}"
            f"|{service.volumetric_divisor}|{_fraction_text(service.max_weight_kg)}"
            f"|{service.status.value}"
        )
    for card in market.rate_cards:
        lines.append(
            f"rate_card|{card.code}|{card.service}|{card.currency.value}"
            f"|{card.effective_from}|{card.effective_to}|{card.fuel.value}"
            f"|{card.status.value}|{card.source}"
        )
    for zone in market.zones:
        lines.append(
            f"zone|{zone.rate_card}|{zone.destination}|{zone.zone}|{zone.status.value}"
        )
    for rate in market.rates:
        lines.append(
            f"rate|{rate.rate_card}|{rate.zone}|{_fraction_text(rate.weight_kg)}"
            f"|{rate.amount_minor}|{rate.amount_max_minor}"
        )
    for surcharge in market.surcharges:
        lines.append(
            f"surcharge|{surcharge.line_no}|{surcharge.service}|{surcharge.rate_card}"
            f"|{surcharge.name}|{surcharge.basis.value}"
            f"|{_fraction_text(surcharge.rate)}|{surcharge.amount_minor}"
            f"|{surcharge.min_amount_minor}|{surcharge.currency.value}"
            f"|{surcharge.trigger.value}|{' '.join(surcharge.destinations)}"
            f"|{surcharge.fuel_applies.value}|{surcharge.valid_until}"
            f"|{surcharge.status.value}|{surcharge.source}"
        )
    for transit in market.transit:
        lines.append(
            f"transit|{transit.service}|{transit.scope.country}|{transit.scope.group}"
            f"|{transit.days_min}|{transit.days_max}|{transit.status.value}"
        )
    for restriction in market.restrictions:
        lines.append(
            f"restriction|{restriction.service}|{restriction.category}"
            f"|{restriction.rule.value}|{restriction.status.value}"
        )
    for route in market.routes:
        lines.append(
            f"route|{route.scope.country}|{route.scope.group}|{route.rate_card}"
            f"|{route.source.value}|{route.standing.value}"
        )
    for fee in market.fees:
        country = fee.scope.country if fee.scope is not None else None
        group = fee.scope.group if fee.scope is not None else None
        fixed = "-"
        if fee.fixed is not None:
            fixed = f"{fee.fixed.amount_minor} {fee.fixed.currency.value}"
        lines.append(
            f"fee|{fee.line_no}|{fee.component.value}|{country}|{group}"
            f"|{_fraction_text(fee.rate)}|{fixed}|{fee.effective_from}"
            f"|{fee.standing.value}|{fee.source}"
        )
    return lines


def _fraction_text(value: Fraction | None) -> str:
    if value is None:
        return "-"
    return f"{value.numerator}/{value.denominator}"


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
