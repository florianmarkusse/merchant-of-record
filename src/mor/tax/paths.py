"""The tax path rule (docs/plan-quote.md §3, plan-logistics.md §4): for one
order into one open country, who collects the indirect tax (VAT, GST) and
whether duty is due at the border, decided separately. Pure.

For each charge the country's rows are walked most specific first (as
`markets.rules.candidate_rates` orders them) and the first whose band
holds the order's value decides. The value is converted into the limit's
currency exactly and compared unrounded.
"""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

from mor.markets.fx import FxMissing, convert_minor
from mor.markets.records import (
    Band,
    Charge,
    Collector,
    LandedRate,
    Limit,
    LimitBasis,
    MarketSet,
    Registration,
)
from mor.markets.rules import Open
from mor.money.amount import Money


@dataclass(frozen=True)
class OrderValue:
    """What the bands measure: the goods, and the freight once known."""

    goods: Money
    freight: Money | None


@dataclass(frozen=True)
class NotDue:
    """`below_limits`: no row's band holds the value. `not_registered`: a
    seller band holds it, but we hold no registration there yet, so
    nothing is due until we do."""

    reason: Literal["below_limits", "not_registered"]
    rate: LandedRate | None  # the seller row, for `not_registered`


@dataclass(frozen=True)
class SellerCollected:
    rate: LandedRate
    registration: Registration


@dataclass(frozen=True)
class BorderPaid:
    rate: LandedRate


type IndirectTaxPath = NotDue | SellerCollected | BorderPaid
type DutyPath = NotDue | BorderPaid


@dataclass(frozen=True)
class TaxPaths:
    indirect_tax: IndirectTaxPath
    duty: DutyPath
    # Other charges whose band holds (additional duty, fees, other taxes),
    # each collected at the border.
    other_border: tuple[LandedRate, ...]


def anything_at_border(paths: TaxPaths) -> bool:
    """Whether customs will charge something: then only duty-paid services
    may carry the order (plan-logistics §4)."""
    if isinstance(paths.indirect_tax, BorderPaid):
        return True
    if isinstance(paths.duty, BorderPaid):
        return True
    return len(paths.other_border) > 0


@dataclass(frozen=True)
class NeedsFreight:
    """A band measures goods plus freight, and the freight is not known
    yet: decided once shipping is priced, never guessed."""

    line_no: int


type TaxPathsResult = TaxPaths | NeedsFreight | FxMissing


def tax_paths(
    market: MarketSet, rules: Open, order: OrderValue, on: date
) -> TaxPathsResult:
    indirect = _deciding_row(market, rules, Charge.IMPORT_TAX, order)
    if isinstance(indirect, NeedsFreight | FxMissing):
        return indirect
    duty = _deciding_row(market, rules, Charge.DUTY, order)
    if isinstance(duty, NeedsFreight | FxMissing):
        return duty
    other: list[LandedRate] = []
    for charge in (Charge.ADDITIONAL_DUTY, Charge.FEE, Charge.OTHER_TAX):
        row = _deciding_row(market, rules, charge, order)
        if isinstance(row, NeedsFreight | FxMissing):
            return row
        if row is not None:
            other.append(row)
    return TaxPaths(
        _indirect_path(market, rules, indirect, on),
        _duty_path(duty),
        tuple(other),
    )


def _indirect_path(
    market: MarketSet, rules: Open, row: LandedRate | None, on: date
) -> IndirectTaxPath:
    if row is None:
        return NotDue("below_limits", None)
    match row.collector:
        case Collector.BORDER:
            return BorderPaid(row)
        case Collector.SELLER:
            registration = registration_on(market, rules.country.iso2, on)
            if registration is None:
                return NotDue("not_registered", row)
            return SellerCollected(row, registration)


def _duty_path(row: LandedRate | None) -> DutyPath:
    # Duty has no seller scheme anywhere (plan-logistics §4): a row decides
    # that it is due at the border, or no row does and it is not.
    if row is None:
        return NotDue("below_limits", None)
    return BorderPaid(row)


def _deciding_row(
    market: MarketSet, rules: Open, charge: Charge, order: OrderValue
) -> LandedRate | None | NeedsFreight | FxMissing:
    for row in rules.candidates.get(charge, ()):
        holds = band_holds(market, row, order)
        if isinstance(holds, NeedsFreight | FxMissing):
            return holds
        if holds:
            return row
    return None


def band_holds(
    market: MarketSet, row: LandedRate, order: OrderValue
) -> bool | NeedsFreight | FxMissing:
    if row.limit is None:
        assert row.band == Band.ALWAYS
        return True
    value = _measured(market, row.limit, order, row.line_no)
    if isinstance(value, NeedsFreight | FxMissing):
        return value
    limit = row.limit.amount.amount_minor
    match row.band:
        case Band.UP_TO:
            return value <= limit
        case Band.ABOVE:
            return value > limit
        case Band.ALWAYS:
            return True


def _measured(
    market: MarketSet, limit: Limit, order: OrderValue, line_no: int
) -> Fraction | NeedsFreight | FxMissing:
    """The order's value as the limit measures it, in the limit's minor
    units, exact."""
    currency = limit.amount.currency
    goods = convert_minor(market, order.goods, currency)
    if isinstance(goods, FxMissing):
        return goods
    match limit.basis:
        case LimitBasis.GOODS:
            return goods
        case LimitBasis.GOODS_PLUS_FREIGHT:
            if order.freight is None:
                return NeedsFreight(line_no)
            freight = convert_minor(market, order.freight, currency)
            if isinstance(freight, FxMissing):
                return freight
            return goods + freight


def registration_on(market: MarketSet, iso2: str, on: date) -> Registration | None:
    """Our registration in a country in force on `on`, if any."""
    for registration in market.registrations:
        if registration.country == iso2 and registration.effective_from <= on:
            return registration
    return None
