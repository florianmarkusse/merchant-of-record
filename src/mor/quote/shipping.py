"""What each routed rate card charges to carry one parcel to a destination,
from our own tables (docs/plan-quote.md §4, 4c-1). Pure.

A card is offered when its route names the destination (or a group it is
in), and everything its price needs is confirmed (Q1): the service, the
card, the zone, the weight break, and every surcharge charged at checkout.
Otherwise it is not offered, with the reason, so the staff page can say
why. The price is the weight break's rate for the zone, plus the
surcharges that always apply, plus fuel on the freight and on the
surcharges fuel applies to. Surcharges charged only on duties paid at the
border are priced once the duties are known (`duty_paid_parts`); those
only on an event, optional or passed through are not charged at checkout
(decided 18-Nov-2026: a person handles them at invoice import).

Every amount is exact, in the quote currency's minor units; the caller
rounds each part with a named mode.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from mor.markets.changes import decimal_text, percent_text
from mor.markets.fx import FxMissing, convert_minor
from mor.markets.records import (
    USABLE_STATUSES,
    CardFuel,
    CardRate,
    CardZone,
    DutiesPaid,
    FuelApplies,
    MarketSet,
    RateCard,
    RestrictionRule,
    Service,
    ShippingRoute,
    Surcharge,
    SurchargeBasis,
    SurchargeTrigger,
    Transit,
)
from mor.money.amount import Money
from mor.money.currency import Currency


FUEL = "fuel"  # the surcharges.csv name of a card's fuel row
GRAMS_PER_KG = 1000
MM3_PER_CM3 = 1000


@dataclass(frozen=True)
class Parcel:
    """One parcel per order (4c): its packed weight and box."""

    weight_g: int
    length_mm: int
    width_mm: int
    height_mm: int


class ShipPartKind(StrEnum):
    FREIGHT = "freight"
    SURCHARGE = "surcharge"
    FUEL = "fuel"
    DUTY_PAID_FEE = "duty_paid_fee"


@dataclass(frozen=True)
class ShipPart:
    kind: ShipPartKind
    name: str
    exact: Fraction  # minor units of the quote currency
    how: str  # how it was taken, for a person
    line_no: int | None  # its row in surcharges.csv


type NotOfferedReason = Literal[
    "service_not_confirmed",
    "card_not_in_force",
    "card_not_confirmed",
    "no_zone",
    "zone_not_confirmed",
    "over_weight",
    "price_range",
    "category_restricted",
    "surcharge_not_confirmed",
    "fuel_unknown",
    "not_duty_paid",
    "fx_missing",
]


@dataclass(frozen=True)
class NotOffered:
    rate_card: str
    reason: NotOfferedReason
    detail: str


@dataclass(frozen=True)
class Offer:
    route: ShippingRoute
    card: RateCard
    service: Service
    zone: CardZone
    chargeable_kg: Fraction
    billed_kg: Fraction
    # Freight, the surcharges that always apply, and fuel: what customs
    # counts as the freight.
    parts: tuple[ShipPart, ...]
    # Charged when something is paid at the border (`duty_paid_parts`).
    duty_paid_rows: tuple[Surcharge, ...]
    fuel_rate: Fraction
    fuel_stale_since: date | None  # the fuel row's last day, when past
    transit: Transit | None


def offers(
    market: MarketSet,
    destination: str,
    categories: frozenset[str],
    parcel: Parcel,
    line_count: int,
    goods_exact: Fraction,
    currency: Currency,
    on: date,
) -> list[Offer | NotOffered]:
    """Every card routed to the destination, priced or not offered, in
    card order. `goods_exact` is the goods value in the quote currency, for
    a surcharge on value."""
    result: list[Offer | NotOffered] = []
    for route in routes_to(market, destination):
        result.append(
            _offer(
                market,
                route,
                destination,
                categories,
                parcel,
                line_count,
                goods_exact,
                currency,
                on,
            )
        )
    return result


def routes_to(market: MarketSet, destination: str) -> list[ShippingRoute]:
    """The routes whose place is the destination or a group it is in, one
    per card (the country's own row before a group's), by card."""
    groups: set[str] = set()
    for group in market.groups:
        if destination in group.members:
            groups.add(group.code)
    chosen: dict[str, ShippingRoute] = {}
    for route in market.routes:
        if route.scope.country == destination:
            chosen[route.rate_card] = route
    for route in market.routes:
        if route.scope.group in groups and route.rate_card not in chosen:
            chosen[route.rate_card] = route
    ordered: list[ShippingRoute] = []
    for code in sorted(chosen):
        ordered.append(chosen[code])
    return ordered


def carries_duties_paid(market: MarketSet, service: Service, destination: str) -> bool:
    """Whether the carrier pays what is due at the border and bills us."""
    match service.duties_paid:
        case DutiesPaid.YES | DutiesPaid.MANDATORY:
            return True
        case DutiesPaid.IOSS_ONLY:
            return _in_places(market, destination, ("EU",))
        case DutiesPaid.ON_REQUEST | DutiesPaid.NO | DutiesPaid.UNKNOWN:
            return False


def chargeable_kg(service: Service, parcel: Parcel) -> Fraction:
    """The greater of the actual and the volumetric weight (L x W x H in
    cm³ over the service's divisor)."""
    actual = Fraction(parcel.weight_g, GRAMS_PER_KG)
    if service.volumetric_divisor is None:
        return actual
    volume_cm3 = Fraction(
        parcel.length_mm * parcel.width_mm * parcel.height_mm, MM3_PER_CM3
    )
    return max(actual, volume_cm3 / service.volumetric_divisor)


def _offer(
    market: MarketSet,
    route: ShippingRoute,
    destination: str,
    categories: frozenset[str],
    parcel: Parcel,
    line_count: int,
    goods_exact: Fraction,
    currency: Currency,
    on: date,
) -> Offer | NotOffered:
    code = route.rate_card
    card = _card(market, code)
    service = _service(market, card.service)
    if service.status not in USABLE_STATUSES:
        return NotOffered(code, "service_not_confirmed", service.status.value)
    if not _in_force(card, on):
        return NotOffered(code, "card_not_in_force", _card_dates(card))
    if card.status not in USABLE_STATUSES:
        return NotOffered(code, "card_not_confirmed", card.status.value)
    for category in sorted(categories):
        restricted = _restriction(market, service.code, category)
        if restricted is not None:
            return NotOffered(code, "category_restricted", restricted)
    zone = _zone(market, code, destination)
    if zone is None:
        return NotOffered(code, "no_zone", destination)
    if zone.status not in USABLE_STATUSES:
        return NotOffered(code, "zone_not_confirmed", zone.status.value)
    chargeable = chargeable_kg(service, parcel)
    if service.max_weight_kg is not None and chargeable > service.max_weight_kg:
        detail = f"{_kg(chargeable)} kg over the service's {_kg(service.max_weight_kg)}"
        return NotOffered(code, "over_weight", detail)
    rate = _weight_break(market, code, zone.zone, chargeable)
    if rate is None:
        detail = f"{_kg(chargeable)} kg above the card's last weight break"
        return NotOffered(code, "over_weight", detail)
    if rate.amount_max_minor is not None:
        return NotOffered(
            code, "price_range", f"zone {zone.zone}, {_kg(rate.weight_kg)} kg"
        )
    freight = convert_minor(market, Money(rate.amount_minor, card.currency), currency)
    if isinstance(freight, FxMissing):
        return NotOffered(
            code, "fx_missing", f"{card.currency.value} to {currency.value}"
        )
    how = f"zone {zone.zone}, {_kg(rate.weight_kg)} kg"
    if chargeable > Fraction(parcel.weight_g, GRAMS_PER_KG):
        how += f" (volumetric {_kg(chargeable)} kg)"
    parts: list[ShipPart] = [
        ShipPart(ShipPartKind.FREIGHT, "freight", freight, how, None)
    ]
    fuel_base = freight
    fuel_names: list[str] = []
    fuel_row: Surcharge | None = None
    duty_paid_rows: list[Surcharge] = []
    for row in surcharges_for(market, card, destination):
        if row.name == FUEL:
            fuel_row = row
            continue
        match row.trigger:
            case SurchargeTrigger.OPTIONAL | SurchargeTrigger.ON_EVENT:
                continue
            case SurchargeTrigger.PASSTHROUGH:
                continue  # a duty or tax passed through: counted as duty and tax
            case SurchargeTrigger.DUTIES_PAID:
                duty_paid_rows.append(row)
                continue
            case SurchargeTrigger.ALWAYS:
                pass
        if row.basis == SurchargeBasis.PERCENT_OF_DUTIES:
            duty_paid_rows.append(row)  # needs the duties: priced with them
            continue
        unusable = _unusable(row)
        if unusable is not None:
            return NotOffered(code, "surcharge_not_confirmed", unusable)
        priced = _surcharge_exact(
            market,
            row,
            rate.weight_kg,
            line_count,
            freight,
            goods_exact,
            currency,
            Fraction(0),
        )
        if isinstance(priced, FxMissing):
            return NotOffered(
                code, "fx_missing", f"{row.currency.value} to {currency.value}"
            )
        if priced is None:
            detail = f"{row.name} has no amount (surcharges.csv line {row.line_no})"
            return NotOffered(code, "surcharge_not_confirmed", detail)
        exact, taken = priced
        parts.append(
            ShipPart(ShipPartKind.SURCHARGE, row.name, exact, taken, row.line_no)
        )
        if row.fuel_applies == FuelApplies.YES:
            fuel_base += exact
            fuel_names.append(row.name)
    fuel = _fuel_rate(card, fuel_row)
    if isinstance(fuel, NotOffered):
        return fuel
    stale: date | None = None
    if fuel_row is not None and fuel_row.valid_until is not None:
        if fuel_row.valid_until < on:
            stale = fuel_row.valid_until
    if card.fuel == CardFuel.EXTRA:
        assert fuel_row is not None and fuel_row.rate is not None
        on_what = " + ".join(["freight", *fuel_names])
        parts.append(
            ShipPart(
                ShipPartKind.FUEL,
                FUEL,
                fuel_base * fuel,
                f"{percent_text(fuel_row.rate)} of {on_what}",
                fuel_row.line_no,
            )
        )
    return Offer(
        route,
        card,
        service,
        zone,
        chargeable,
        rate.weight_kg,
        tuple(parts),
        tuple(duty_paid_rows),
        fuel,
        stale,
        _transit(market, service.code, destination),
    )


def duty_paid_parts(
    market: MarketSet,
    offer: Offer,
    at_border_exact: Fraction,
    line_count: int,
    goods_exact: Fraction,
    currency: Currency,
) -> list[ShipPart] | NotOffered:
    """The carrier's charges for paying the duties and taxes at the border
    (`at_border_exact`, in the quote currency), with fuel where it applies.
    Called only when something is paid at the border."""
    parts: list[ShipPart] = []
    code = offer.card.code
    for row in offer.duty_paid_rows:
        unusable = _unusable(row)
        if unusable is not None:
            return NotOffered(code, "surcharge_not_confirmed", unusable)
        priced = _surcharge_exact(
            market,
            row,
            offer.billed_kg,
            line_count,
            offer.parts[0].exact,  # the freight
            goods_exact,
            currency,
            at_border_exact,
        )
        if isinstance(priced, FxMissing):
            return NotOffered(
                code, "fx_missing", f"{row.currency.value} to {currency.value}"
            )
        if priced is None:
            detail = f"{row.name} has no amount (surcharges.csv line {row.line_no})"
            return NotOffered(code, "surcharge_not_confirmed", detail)
        exact, taken = priced
        parts.append(
            ShipPart(ShipPartKind.DUTY_PAID_FEE, row.name, exact, taken, row.line_no)
        )
        if row.fuel_applies == FuelApplies.YES and offer.fuel_rate:
            parts.append(
                ShipPart(
                    ShipPartKind.FUEL,
                    f"{FUEL} on {row.name}",
                    exact * offer.fuel_rate,
                    f"{percent_text(offer.fuel_rate)} of {row.name}",
                    row.line_no,
                )
            )
    return parts


def surcharges_for(
    market: MarketSet, card: RateCard, destination: str
) -> list[Surcharge]:
    """The surcharges on a card's prices into a destination: rows for every
    card of the service, except where the card has its own row of that
    name, and no fuel row when the card's prices include fuel (as
    `scripts/markets.py` reads them)."""
    service_rows: list[Surcharge] = []
    own_names: set[str] = set()
    for row in market.surcharges:
        if row.service != card.service:
            continue
        if row.rate_card is not None and row.rate_card != card.code:
            continue
        if row.destinations and not _in_places(market, destination, row.destinations):
            continue
        service_rows.append(row)
        if row.rate_card is not None:
            own_names.add(row.name)
    kept: list[Surcharge] = []
    for row in service_rows:
        if row.rate_card is None and row.name in own_names:
            continue
        if row.name == FUEL and card.fuel == CardFuel.INCLUDED:
            continue
        kept.append(row)
    return kept


def _fuel_rate(card: RateCard, row: Surcharge | None) -> Fraction | NotOffered:
    match card.fuel:
        case CardFuel.INCLUDED:
            return Fraction(0)
        case CardFuel.UNKNOWN:
            return NotOffered(card.code, "fuel_unknown", "the card does not say")
        case CardFuel.EXTRA:
            pass
    if row is None:
        return NotOffered(card.code, "fuel_unknown", "no fuel row for the service")
    if row.status not in USABLE_STATUSES or row.rate is None:
        detail = f"fuel is {row.status.value} (surcharges.csv line {row.line_no})"
        return NotOffered(card.code, "fuel_unknown", detail)
    return row.rate


def _unusable(row: Surcharge) -> str | None:
    if row.status not in USABLE_STATUSES:
        return f"{row.name} is {row.status.value} (surcharges.csv line {row.line_no})"
    if row.fuel_applies == FuelApplies.UNKNOWN:
        return f"fuel on {row.name} is unknown (surcharges.csv line {row.line_no})"
    return None


def _surcharge_exact(
    market: MarketSet,
    row: Surcharge,
    billed_kg: Fraction,
    line_count: int,
    freight: Fraction,
    goods: Fraction,
    currency: Currency,
    at_border: Fraction,
) -> tuple[Fraction, str] | None | FxMissing:
    """A surcharge in the quote currency's minor units, with how it was
    taken; None when the row lacks the number its basis needs."""
    amount: Fraction | None = None
    if row.amount_minor is not None:
        converted = convert_minor(
            market, Money(row.amount_minor, row.currency), currency
        )
        if isinstance(converted, FxMissing):
            return converted
        amount = converted
    minimum: Fraction | None = None
    if row.min_amount_minor is not None:
        converted = convert_minor(
            market, Money(row.min_amount_minor, row.currency), currency
        )
        if isinstance(converted, FxMissing):
            return converted
        minimum = converted
    match row.basis:
        case SurchargeBasis.PER_SHIPMENT | SurchargeBasis.PER_PIECE:
            if amount is None:
                return None
            return amount, row.basis.value.replace("_", " ")
        case SurchargeBasis.PER_KG:
            if amount is None:
                return None
            return _at_least(amount * billed_kg, minimum, f"{_kg(billed_kg)} kg")
        case SurchargeBasis.PER_LINE:
            if amount is None:
                return None
            return amount * line_count, f"{line_count} line(s)"
        case SurchargeBasis.PERCENT_OF_FREIGHT:
            if row.rate is None:
                return None
            return _at_least(
                freight * row.rate, minimum, f"{percent_text(row.rate)} of freight"
            )
        case SurchargeBasis.PERCENT_OF_VALUE:
            if row.rate is None:
                return None
            return _at_least(
                goods * row.rate, minimum, f"{percent_text(row.rate)} of value"
            )
        case SurchargeBasis.PERCENT_OF_DUTIES:
            if row.rate is None:
                return None
            taken = f"{percent_text(row.rate)} of duties and taxes"
            return _at_least(at_border * row.rate, minimum, taken)


def _at_least(
    value: Fraction, minimum: Fraction | None, how: str
) -> tuple[Fraction, str]:
    if minimum is not None and value < minimum:
        return minimum, f"the minimum ({how} is less)"
    return value, how


def _restriction(market: MarketSet, service: str, category: str) -> str | None:
    """Why the service may not carry the category, or None. A row that is
    not a confirmed `allowed` keeps the service out (Q1)."""
    for row in market.restrictions:
        if row.service != service or row.category != category:
            continue
        if row.rule != RestrictionRule.ALLOWED:
            return f"{category}: {row.rule.value} ({row.status.value})"
        if row.status not in USABLE_STATUSES:
            return f"{category}: allowed, but {row.status.value}"
    return None


def _in_force(card: RateCard, on: date) -> bool:
    if card.effective_from is not None and card.effective_from > on:
        return False
    return card.effective_to is None or card.effective_to >= on


def _card_dates(card: RateCard) -> str:
    return f"from {card.effective_from or 'any day'} to {card.effective_to or 'open'}"


def _card(market: MarketSet, code: str) -> RateCard:
    for card in market.rate_cards:
        if card.code == code:
            return card
    raise AssertionError(f"route to unknown card {code}: the loader checks this")


def _service(market: MarketSet, code: str) -> Service:
    for service in market.services:
        if service.code == code:
            return service
    raise AssertionError(f"card of unknown service {code}: the loader checks this")


def _zone(market: MarketSet, card: str, destination: str) -> CardZone | None:
    for zone in market.zones:
        if zone.rate_card == card and zone.destination == destination:
            return zone
    return None


def _weight_break(
    market: MarketSet, card: str, zone: str, chargeable: Fraction
) -> CardRate | None:
    """The card's smallest weight break at or above the chargeable weight."""
    best: CardRate | None = None
    for rate in market.rates:
        if rate.rate_card != card or rate.zone != zone or rate.weight_kg < chargeable:
            continue
        if best is None or rate.weight_kg < best.weight_kg:
            best = rate
    return best


def _transit(market: MarketSet, service: str, destination: str) -> Transit | None:
    """The service's transit time to the country, else to a group it is in."""
    in_group: Transit | None = None
    for transit in market.transit:
        if transit.service != service:
            continue
        if transit.scope.country == destination:
            return transit
        group = transit.scope.group
        if group is not None and in_group is None:
            if _in_places(market, destination, (group,)):
                in_group = transit
    return in_group


def _in_places(market: MarketSet, destination: str, places: tuple[str, ...]) -> bool:
    if destination in places:
        return True
    for group in market.groups:
        if group.code in places and destination in group.members:
            return True
    return False


def _kg(value: Fraction) -> str:
    return decimal_text(value)
