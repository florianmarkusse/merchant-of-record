"""What each routed service charges to carry one parcel to a destination
(docs/plan-quote.md §4): from our own rate card (4c-1) or from a price the
carrier gave (4c-2). Pure.

A service is offered when its route names the destination (or a group it
is in), and everything its price needs is confirmed (Q1). Otherwise it is
not offered, with the reason, so the staff page can say why.

- From our card: the card, the zone, the weight break and every surcharge
  charged at checkout must be confirmed. The price is the weight break's
  rate for the zone, plus the surcharges that always apply, plus fuel on
  the freight and on the surcharges fuel applies to.
- From the carrier: the price we hold for the exact parcel, while usable
  (`PriceHeld`); without one the price is missing (`PriceMissing`), and
  the quote waits for the worker to ask. Every part the carrier gave is
  charged: it is what the carrier says it will bill.

Either way, surcharges charged only on duties paid at the border are
ours, priced once the duties are known (`duty_paid_parts`); those only on
an event, optional or passed through are not charged at checkout
(decided 18-Nov-2026: a person handles them at invoice import).

Every amount is exact, in the quote currency's minor units; the caller
rounds each part with a named mode.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from fractions import Fraction
from typing import Literal

from mor.couriers.records import (
    FinishedLookup,
    OwnPrice,
    Parcel,
    PriceHeld,
    PricePartKind,
    PriceQuestion,
    PriceState,
)
from mor.dates import format_moment
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
    PriceSource,
    RateCard,
    RestrictionRule,
    Scope,
    Service,
    ShippingRoute,
    Standing,
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
    "carrier_not_served",
    "carrier_needs_person",
    "placeholder",  # left out in prod (mor.quote.refusals.offered_in)
]


@dataclass(frozen=True)
class NotOffered:
    key: str  # the route's key: its card, or `live:<service>`
    reason: NotOfferedReason
    detail: str


@dataclass(frozen=True)
class FromCard:
    card: RateCard
    zone: CardZone


@dataclass(frozen=True)
class FromCarrier:
    """A price the carrier gave, as we hold it."""

    lookup_id: uuid.UUID
    finished_at: datetime
    usable_until: datetime
    carrier_total: Money
    own: OwnPrice | None  # our card's price when the carrier answered


@dataclass(frozen=True)
class Offer:
    route: ShippingRoute
    service: Service
    priced_by: FromCard | FromCarrier
    chargeable_kg: Fraction
    billed_kg: Fraction
    # Freight, the surcharges that always apply, and fuel: what customs
    # counts as the freight.
    parts: tuple[ShipPart, ...]
    # Charged when something is paid at the border (`duty_paid_parts`).
    duty_paid_rows: tuple[Surcharge, ...]
    # Fuel on a duty-paid fee. From the carrier: our fuel row for the
    # service, None when it has no usable one.
    fuel_rate: Fraction | None
    fuel_stale_since: date | None  # the fuel row's last day, when past
    transit: Transit | None


@dataclass(frozen=True)
class PriceMissing:
    """A carrier route without a usable price: ask (`open_id` None) or wait
    for the request already open."""

    question: PriceQuestion
    open_id: uuid.UUID | None


def offers(
    market: MarketSet,
    destination: str,
    categories: frozenset[str],
    parcel: Parcel,
    line_count: int,
    goods_exact: Fraction,
    currency: Currency,
    on: date,
    prices: dict[PriceQuestion, PriceHeld],
    now: datetime,
) -> list[Offer | NotOffered | PriceMissing]:
    """Every service routed to the destination, priced, not offered or
    waiting for the carrier, in route order. `goods_exact` is the goods
    value in the quote currency, for a surcharge on value; `prices` what we
    hold from carriers, compared with `now`."""
    result: list[Offer | NotOffered | PriceMissing] = []
    for route in routes_to(market, destination):
        match route.source:
            case PriceSource.OWN_TABLE:
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
            case PriceSource.CARRIER_API:
                result.append(
                    _live_offer(
                        market,
                        route,
                        destination,
                        categories,
                        parcel,
                        currency,
                        prices,
                        now,
                    )
                )
    return result


def routes_to(market: MarketSet, destination: str) -> list[ShippingRoute]:
    """The routes whose place is the destination or a group it is in, one
    per service (the country's own row before a group's), by key."""
    groups: set[str] = set()
    for group in market.groups:
        if destination in group.members:
            groups.add(group.code)
    chosen: dict[str, ShippingRoute] = {}
    for route in market.routes:
        if route.scope.country == destination:
            chosen[route.service] = route
    for route in market.routes:
        if route.scope.group in groups and route.service not in chosen:
            chosen[route.service] = route
    ordered = list(chosen.values())
    ordered.sort(key=lambda route: route.key)
    return ordered


def price_question(service: Service, destination: str, parcel: Parcel) -> PriceQuestion:
    return PriceQuestion(
        service.provider, service.code, service.origin, destination, parcel
    )


def own_card_price(
    market: MarketSet,
    service_code: str,
    destination: str,
    parcel: Parcel,
    currency: Currency,
    on: date,
) -> tuple[str, Fraction] | None:
    """Our own price for a service, lane and parcel, from the first of its
    cards in force that can price it (by code), whether routed or not: for
    comparing a carrier's price with. Freight, the surcharges that always
    apply, and fuel; with no goods value and one line, so a surcharge on
    value counts as zero."""
    codes: list[str] = []
    for card in market.rate_cards:
        if card.service == service_code:
            codes.append(card.code)
    for code in sorted(codes):
        route = ShippingRoute(
            Scope(destination, None),
            service_code,
            code,
            PriceSource.OWN_TABLE,
            Standing.PLACEHOLDER,
        )
        offer = _offer(
            market,
            route,
            destination,
            frozenset(),
            parcel,
            1,
            Fraction(0),
            currency,
            on,
        )
        if isinstance(offer, Offer):
            total = Fraction(0)
            for part in offer.parts:
                total += part.exact
            return code, total
    return None


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
    code = route.key
    assert route.rate_card is not None, "an own_table route names its card"
    card = _card(market, route.rate_card)
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
    for row in surcharges_for(market, service.code, card, destination):
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
        service,
        FromCard(card, zone),
        chargeable,
        rate.weight_kg,
        tuple(parts),
        tuple(duty_paid_rows),
        fuel,
        stale,
        _transit(market, service.code, destination),
    )


def _live_offer(
    market: MarketSet,
    route: ShippingRoute,
    destination: str,
    categories: frozenset[str],
    parcel: Parcel,
    currency: Currency,
    prices: dict[PriceQuestion, PriceHeld],
    now: datetime,
) -> Offer | NotOffered | PriceMissing:
    """A carrier route: what stays ours is checked first (the service, the
    categories it carries, its maximum weight), so a carrier is never asked
    for a parcel we would not send; then the price we hold."""
    code = route.key
    service = _service(market, route.service)
    if service.status not in USABLE_STATUSES:
        return NotOffered(code, "service_not_confirmed", service.status.value)
    for category in sorted(categories):
        restricted = _restriction(market, service.code, category)
        if restricted is not None:
            return NotOffered(code, "category_restricted", restricted)
    chargeable = chargeable_kg(service, parcel)
    if service.max_weight_kg is not None and chargeable > service.max_weight_kg:
        detail = f"{_kg(chargeable)} kg over the service's {_kg(service.max_weight_kg)}"
        return NotOffered(code, "over_weight", detail)
    question = price_question(service, destination, parcel)
    held = prices.get(question, PriceHeld(None, None))
    latest = held.latest
    if latest is None or latest.usable_until <= now:
        return PriceMissing(question, held.open_id)
    match latest.state:
        case PriceState.NOT_SERVED:
            return NotOffered(code, "carrier_not_served", latest.detail or "")
        case PriceState.NEEDS_PERSON:
            return NotOffered(code, "carrier_needs_person", latest.detail or "")
        case PriceState.REQUESTED:
            raise AssertionError("a finished lookup is never requested")
        case PriceState.ANSWERED:
            pass
    return _from_carrier(
        market, route, service, destination, latest, chargeable, currency
    )


def _from_carrier(
    market: MarketSet,
    route: ShippingRoute,
    service: Service,
    destination: str,
    latest: FinishedLookup,
    chargeable: Fraction,
    currency: Currency,
) -> Offer | NotOffered:
    code = route.key
    assert latest.price is not None, "an answered lookup has its price"
    carrier_currency = latest.price.total.currency
    asked = f"the carrier's price, asked {format_moment(latest.finished_at)}"
    parts: list[ShipPart] = []
    for part in latest.price.parts:
        exact = convert_minor(
            market, Money(part.amount_minor, carrier_currency), currency
        )
        if isinstance(exact, FxMissing):
            return NotOffered(
                code, "fx_missing", f"{carrier_currency.value} to {currency.value}"
            )
        match part.kind:
            case PricePartKind.FREIGHT:
                kind = ShipPartKind.FREIGHT
            case PricePartKind.FUEL:
                kind = ShipPartKind.FUEL
            case PricePartKind.SURCHARGE:
                kind = ShipPartKind.SURCHARGE
        parts.append(ShipPart(kind, part.name, exact, asked, None))
    duty_paid_rows: list[Surcharge] = []
    fuel_row: Surcharge | None = None
    for row in surcharges_for(market, service.code, None, destination):
        if row.name == FUEL:
            fuel_row = row
            continue
        if row.trigger == SurchargeTrigger.DUTIES_PAID or (
            row.trigger == SurchargeTrigger.ALWAYS
            and row.basis == SurchargeBasis.PERCENT_OF_DUTIES
        ):
            duty_paid_rows.append(row)
    fuel_rate: Fraction | None = None
    if (
        fuel_row is not None
        and fuel_row.status in USABLE_STATUSES
        and fuel_row.rate is not None
    ):
        fuel_rate = fuel_row.rate
    return Offer(
        route,
        service,
        FromCarrier(
            latest.id,
            latest.finished_at,
            latest.usable_until,
            latest.price.total,
            latest.price.own,
        ),
        chargeable,
        chargeable,
        tuple(parts),
        tuple(duty_paid_rows),
        fuel_rate,
        None,
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
    code = offer.route.key
    freight = Fraction(0)
    for part in offer.parts:
        if part.kind == ShipPartKind.FREIGHT:
            freight += part.exact
    for row in offer.duty_paid_rows:
        unusable = _unusable(row)
        if unusable is not None:
            return NotOffered(code, "surcharge_not_confirmed", unusable)
        priced = _surcharge_exact(
            market,
            row,
            offer.billed_kg,
            line_count,
            freight,
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
        if row.fuel_applies == FuelApplies.YES and offer.fuel_rate is None:
            detail = f"no usable fuel row for {offer.service.code}"
            return NotOffered(code, "fuel_unknown", detail)
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
    market: MarketSet, service: str, card: RateCard | None, destination: str
) -> list[Surcharge]:
    """The surcharges on a service's prices into a destination: rows for
    every card of the service, except where the card has its own row of
    that name, and no fuel row when the card's prices include fuel (as
    `scripts/markets.py` reads them). Without a card (a carrier's price),
    only the rows for every card."""
    service_rows: list[Surcharge] = []
    own_names: set[str] = set()
    for row in market.surcharges:
        if row.service != service:
            continue
        if row.rate_card is not None and (card is None or row.rate_card != card.code):
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
        if row.name == FUEL and card is not None and card.fuel == CardFuel.INCLUDED:
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
