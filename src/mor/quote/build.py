"""The quote for one order into one country: one option per shipping
service, each with its full total (docs/plan-quote.md §4, 4c-1). Pure:
every price and rate is a plain input, from the market load and the
vendor lookups we hold.

How a total is built (plan-architecture §8, decided 18-Nov-2026):

1. The merchant's price per line, converted to the buyer's currency
   rounding up, so the merchant is never short.
2. Our fee components, each a percentage of the merchant's price (plus
   an optional fixed amount), rounded half to even. They sit inside the
   goods value the buyer pays, so customs and the tax bands see them.
3. Per routed service: freight, the surcharges that always apply, and
   fuel, from our card or the carrier's price, each rounded up (we never
   under-collect the carrier's bill). That is the freight customs counts.
4. The tax paths (4b) with that goods value and freight, per category in
   the order. A card that is not duty-paid cannot carry an order on which
   anything is due at the border.
5. Per line, duty-type charges first, then the taxes on goods, freight and
   duty, each rounded half to even. The freight is shared over the lines
   by their goods value.
6. When anything is paid at the border, the carrier's fee for paying it.

Every part keeps its exact value beside the rounded amount: the
difference is the rounding remainder, booked when the order is paid
(slice 5). The parts sum to the total exactly.
"""

from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from mor.carriers.records import Parcel, PriceHeld, PriceQuestion
from mor.duties.port import RateQuestion
from mor.duties.records import VendorLookup
from mor.ids import (
    CountryCode,
    DutyLookupId,
    HsCode,
    ServiceCode,
    ShippingPriceLookupId,
)
from mor.markets.changes import BASE_WORDS, percent_text
from mor.markets.fx import FxMissing, convert_minor
from mor.markets.records import (
    Base,
    Charge,
    Collector,
    FeeComponent,
    LandedRate,
    MarketSet,
    Percent,
    PerLine,
    PerParcel,
    ServiceTier,
    Standing,
)
from mor.markets.rules import Closed, Open, country_rules
from mor.money.amount import Money, format_major
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode, round_fraction
from mor.quote.fees import fee_rule_for
from mor.quote.groups import PartKind
from mor.quote.shipping import (
    FromCard,
    FromCarrier,
    NotOffered,
    NotOfferedReason,
    Offer,
    PriceMissing,
    ShipPart,
    carries_duties_paid,
    duty_paid_parts,
    offers,
)
from mor.tax.line_rates import (
    AskVendor,
    Found,
    FromBandRow,
    FromHsRow,
    FromVendor,
    Line,
    RateNeeded,
    RateNotConfirmed,
    VendorNeedsPerson,
    VendorUnknownCode,
    VendorWaiting,
    line_rate,
)
from mor.tax.paths import (
    BorderPaid,
    NeedsFreight,
    NotDue,
    OrderValue,
    SellerCollected,
    TaxPaths,
    anything_at_border,
    tax_paths,
)


GOODS_ROUNDING = RoundingMode.CEILING  # the merchant is never short
FEE_ROUNDING = RoundingMode.HALF_EVEN
SHIPPING_ROUNDING = RoundingMode.CEILING  # never under-collect the carrier
CHARGE_ROUNDING = RoundingMode.HALF_EVEN


@dataclass(frozen=True, kw_only=True)
class QuoteLine:
    category: str
    hs_code: HsCode | None
    origin: CountryCode
    quantity: int
    unit_price: Money  # the merchant's price, tax-exclusive, in its currency


@dataclass(frozen=True, kw_only=True)
class QuoteRequest:
    destination: CountryCode
    lines: tuple[QuoteLine, ...]
    parcel: Parcel
    on: date  # which rules and cards are in force
    now: datetime  # a carrier's price is used only before its usable_until


@dataclass(frozen=True, kw_only=True)
class Part:
    kind: PartKind
    label: str  # how it was reached, for a person (its order line: `line`)
    line: int | None  # the order line (0 first), for per-line parts
    exact: Fraction  # minor units of the quote currency
    amount: Money  # rounded by `mode`
    mode: RoundingMode
    collector: Collector | None  # duties and taxes: the border or us
    source: str  # where the number came from, for a person


@dataclass(frozen=True, kw_only=True)
class ShippingOption:
    key: str  # the route's: its card, or `live:<service>`
    rate_card: str | None  # None: priced by the carrier
    live: FromCarrier | None  # the carrier's price it used
    service: ServiceCode
    service_name: str
    tier: ServiceTier
    transit_days: tuple[int, int] | None
    parts: tuple[Part, ...]  # shipping, then duties and taxes
    total: Money  # the quote's goods parts plus these
    fuel_stale_since: date | None
    standing: Standing


@dataclass(frozen=True, kw_only=True)
class Quote:
    destination: CountryCode
    currency: Currency
    goods: tuple[Part, ...]  # each line's price, then the fee components
    options: tuple[ShippingOption, ...]  # cheapest first
    not_offered: tuple[NotOffered, ...]
    placeholders: tuple[str, ...]  # fee rules standing in; prod refuses (4d)


# --- refusals and missing inputs ---------------------------------------------------


@dataclass(frozen=True)
class CountryClosed:
    category: str
    closed: Closed


@dataclass(frozen=True)
class NoCurrency:
    """The destination has no currency we can charge in."""

    currency: str | None


@dataclass(frozen=True)
class NoFeeRule:
    component: FeeComponent


type LineRateProblem = (
    RateNeeded | RateNotConfirmed | VendorUnknownCode | VendorNeedsPerson
)


@dataclass(frozen=True)
class LineRateRefused:
    line: int
    charge: Charge
    problem: LineRateProblem


@dataclass(frozen=True)
class NoServiceForLane:
    not_offered: tuple[NotOffered, ...]


type QuoteRefusal = (
    CountryClosed
    | NoCurrency
    | FxMissing
    | NoFeeRule
    | LineRateRefused
    | NoServiceForLane
)


@dataclass(frozen=True)
class VendorAsk:
    vendor: str
    question: RateQuestion


@dataclass(frozen=True, kw_only=True)
class InputsMissing:
    """A rate a vendor decides, or a carrier's price, is not held yet: ask
    (`asks`, `price_asks`) or wait for an answer already asked for
    (`waiting`, `price_waiting`), then quote again."""

    asks: tuple[VendorAsk, ...]
    waiting: tuple[DutyLookupId, ...]
    price_asks: tuple[PriceQuestion, ...] = ()
    price_waiting: tuple[ShippingPriceLookupId, ...] = ()


type QuoteResult = Quote | QuoteRefusal | InputsMissing


def build_quote(
    market: MarketSet,
    request: QuoteRequest,
    lookups: dict[RateQuestion, VendorLookup],
    prices: dict[PriceQuestion, PriceHeld],
) -> QuoteResult:
    """`lookups`: what we hold from the destination's vendor, per question;
    `prices`: what we hold from carriers, per question. Anything missing
    from either, for any option, is gathered into one `InputsMissing`."""
    assert request.lines, "an order has lines: the edge checks this"
    destination = request.destination
    rules: dict[str, Open] = {}
    for line in request.lines:
        if line.category in rules:
            continue
        answer = country_rules(market, destination, line.category, request.on)
        if not isinstance(answer, Open):
            return CountryClosed(line.category, answer)
        rules[line.category] = answer
    currency = _currency(rules[request.lines[0].category].country.currency)
    if currency is None:
        return NoCurrency(rules[request.lines[0].category].country.currency)

    goods_parts: list[Part] = []
    merchant_exact = Fraction(0)
    for index, line in enumerate(request.lines):
        price = Money(
            line.unit_price.amount_minor * line.quantity, line.unit_price.currency
        )
        exact = convert_minor(market, price, currency)
        if isinstance(exact, FxMissing):
            return exact
        merchant_exact += exact
        goods_parts.append(
            _part(
                PartKind.GOODS,
                f"{line.quantity} × {line.category}",
                index,
                exact,
                currency,
                GOODS_ROUNDING,
                None,
                "the merchant's price",
            )
        )
    placeholders: list[str] = []
    fee_total = 0
    for component in FeeComponent:
        rule = fee_rule_for(market, component, destination, request.on)
        if rule is None:
            return NoFeeRule(component)
        exact = merchant_exact * rule.rate
        if rule.fixed is not None:
            fixed = convert_minor(market, rule.fixed, currency)
            if isinstance(fixed, FxMissing):
                return fixed
            exact += fixed
        how = f"{percent_text(rule.rate)} of the merchant's price"
        if rule.fixed is not None:
            how += f" + {format_major(rule.fixed)} {rule.fixed.currency.value}"
        part = _part(
            PartKind(component.value),
            how,
            None,
            exact,
            currency,
            FEE_ROUNDING,
            None,
            f"fees.csv line {rule.line_no}",
        )
        goods_parts.append(part)
        fee_total += part.amount.amount_minor
        if rule.standing == Standing.PLACEHOLDER:
            placeholders.append(f"fees.csv line {rule.line_no} ({component.value})")

    # The goods value per line as the buyer pays it: the merchant's price
    # plus its share of our fees.
    line_goods = _line_goods(goods_parts, len(request.lines), fee_total)
    goods_value = Money(sum(_rounded(goods_parts)), currency)

    categories = frozenset(rules)
    options: list[ShippingOption] = []
    not_offered: list[NotOffered] = []
    asks: list[VendorAsk] = []
    waiting: list[DutyLookupId] = []
    price_asks: list[PriceQuestion] = []
    price_waiting: list[ShippingPriceLookupId] = []
    for offer in offers(
        market,
        destination,
        categories,
        request.parcel,
        len(request.lines),
        merchant_exact,
        currency,
        request.on,
        prices,
        request.now,
    ):
        if isinstance(offer, NotOffered):
            not_offered.append(offer)
            continue
        if isinstance(offer, PriceMissing):
            if offer.open_id is not None:
                _add_once(price_waiting, offer.open_id)
            else:
                _add_once(price_asks, offer.question)
            continue
        option = _option(
            market,
            request,
            lookups,
            rules,
            offer,
            currency,
            goods_parts,
            line_goods,
            goods_value,
            merchant_exact,
        )
        if isinstance(option, NotOffered):
            not_offered.append(option)
            continue
        if isinstance(option, InputsMissing):
            for ask in option.asks:
                _add_once(asks, ask)
            for lookup_id in option.waiting:
                _add_once(waiting, lookup_id)
            continue
        if not isinstance(option, ShippingOption):
            return option
        options.append(option)
    if asks or waiting or price_asks or price_waiting:
        return InputsMissing(
            asks=tuple(asks),
            waiting=tuple(waiting),
            price_asks=tuple(price_asks),
            price_waiting=tuple(price_waiting),
        )
    if not options:
        return NoServiceForLane(tuple(not_offered))
    options.sort(key=lambda option: (option.total.amount_minor, option.key))
    return Quote(
        destination=destination,
        currency=currency,
        goods=tuple(goods_parts),
        options=tuple(options),
        not_offered=tuple(not_offered),
        placeholders=tuple(placeholders),
    )


def _option(
    market: MarketSet,
    request: QuoteRequest,
    lookups: dict[RateQuestion, VendorLookup],
    rules: dict[str, Open],
    offer: Offer,
    currency: Currency,
    goods_parts: list[Part],
    line_goods: list[Fraction],
    goods_value: Money,
    merchant_exact: Fraction,
) -> ShippingOption | NotOffered | QuoteRefusal | InputsMissing:
    key = offer.route.key
    live: FromCarrier | None = None
    match offer.priced_by:
        case FromCarrier():
            live = offer.priced_by
            ship_source = f"{offer.service.carrier} rate call"
        case FromCard(card=priced_card):
            ship_source = priced_card.code
    shipping: list[Part] = []
    for ship in offer.parts:
        shipping.append(_ship_part(ship, ship_source, currency))
    freight = Money(sum(_rounded(shipping)), currency)
    paths: dict[str, TaxPaths] = {}
    at_border = False
    for category, open_rules in rules.items():
        found = tax_paths(
            market, open_rules, OrderValue(goods_value, freight), request.on
        )
        if isinstance(found, FxMissing):
            return found
        if isinstance(found, NeedsFreight):
            raise AssertionError("the freight is passed in")
        paths[category] = found
        if anything_at_border(found):
            at_border = True
    if at_border and not carries_duties_paid(
        market, offer.service, request.destination
    ):
        return NotOffered(
            key, NotOfferedReason.NOT_DUTY_PAID, "something is due at the border"
        )

    charges = _charges(market, request, lookups, paths, line_goods, freight, currency)
    if not isinstance(charges, list):
        return charges
    border_exact = Fraction(0)
    for charge in charges:
        if charge.collector == Collector.BORDER:
            border_exact += charge.exact
    duty_paid: list[Part] = []
    if at_border:
        fees = duty_paid_parts(
            market, offer, border_exact, len(request.lines), merchant_exact, currency
        )
        if isinstance(fees, NotOffered):
            return fees
        for ship in fees:
            duty_paid.append(_ship_part(ship, ship_source, currency))
    parts = shipping + duty_paid + charges
    total = sum(_rounded(goods_parts)) + sum(_rounded(parts))
    transit = None
    if offer.transit is not None:
        transit = (offer.transit.days_min, offer.transit.days_max)
    return ShippingOption(
        key=key,
        rate_card=offer.route.rate_card,
        live=live,
        service=offer.service.code,
        service_name=offer.service.name,
        tier=offer.service.tier,
        transit_days=transit,
        parts=tuple(parts),
        total=Money(total, currency),
        fuel_stale_since=offer.fuel_stale_since,
        standing=offer.route.standing,
    )


def _charges(
    market: MarketSet,
    request: QuoteRequest,
    lookups: dict[RateQuestion, VendorLookup],
    paths: dict[str, TaxPaths],
    line_goods: list[Fraction],
    freight: Money,
    currency: Currency,
) -> list[Part] | QuoteRefusal | InputsMissing:
    """The duties and taxes per line: duty-type charges first, then the
    taxes, whose base may include them."""
    total_goods = sum(line_goods, Fraction(0))
    parts: list[Part] = []
    asks: list[VendorAsk] = []
    waiting: list[DutyLookupId] = []
    parcel_rows: set[int] = set()  # per-parcel rows already charged
    for index, line in enumerate(request.lines):
        line_paths = paths[line.category]
        goods = line_goods[index]
        share = goods / total_goods if total_goods else Fraction(1, len(line_goods))
        values = _Values(goods, freight.amount_minor * share, Fraction(0))
        rate_line = Line(line.hs_code, line.origin, frozenset())
        question = None
        if line.hs_code is not None:
            question = RateQuestion(request.destination, line.origin, line.hs_code)
        lookup = lookups.get(question) if question is not None else None

        due: list[tuple[Charge, LandedRate]] = []
        if isinstance(line_paths.duty, BorderPaid):
            due.append((Charge.DUTY, line_paths.duty.rate))
        for row in line_paths.other_border:
            if row.charge in (Charge.ADDITIONAL_DUTY, Charge.FEE):
                due.append((row.charge, row))
        match line_paths.indirect_tax:
            case BorderPaid(rate=row) | SellerCollected(rate=row):
                due.append((Charge.IMPORT_TAX, row))
            case NotDue():
                pass
        for row in line_paths.other_border:
            if row.charge == Charge.OTHER_TAX:
                due.append((row.charge, row))

        for charge, row in due:
            if isinstance(row.amount, PerParcel) and row.line_no in parcel_rows:
                continue
            priced = _charge(
                market, request, charge, row, rate_line, lookup, values, currency
            )
            match priced:
                case AskVendor(vendor=vendor):
                    assert question is not None
                    ask = VendorAsk(vendor, question)
                    if ask not in asks:
                        asks.append(ask)
                    continue
                case VendorWaiting(lookup_id=lookup_id):
                    if lookup_id not in waiting:
                        waiting.append(lookup_id)
                    continue
                case (
                    RateNeeded()
                    | RateNotConfirmed()
                    | VendorUnknownCode()
                    | VendorNeedsPerson()
                ):
                    return LineRateRefused(index, charge, priced)
                case FxMissing():
                    return priced
                case Priced(exact=exact, how=how, source=source):
                    pass
            if isinstance(row.amount, PerParcel):
                parcel_rows.add(row.line_no)
            if charge in (Charge.DUTY, Charge.ADDITIONAL_DUTY, Charge.FEE):
                values = _Values(values.goods, values.freight, values.duty + exact)
            parts.append(
                _part(
                    _CHARGE_KINDS[charge],
                    how,
                    None if isinstance(row.amount, PerParcel) else index,
                    exact,
                    currency,
                    CHARGE_ROUNDING,
                    row.collector,
                    source,
                )
            )
    if asks or waiting:
        return InputsMissing(asks=tuple(asks), waiting=tuple(waiting))
    return parts


_CHARGE_KINDS = {
    Charge.DUTY: PartKind.DUTY,
    Charge.ADDITIONAL_DUTY: PartKind.ADDITIONAL_DUTY,
    Charge.FEE: PartKind.CUSTOMS_FEE,
    Charge.IMPORT_TAX: PartKind.IMPORT_TAX,
    Charge.OTHER_TAX: PartKind.OTHER_TAX,
}


@dataclass(frozen=True)
class Priced:
    exact: Fraction
    how: str  # the rate and what it is taken on, for a person
    source: str  # where the rate or amount came from


@dataclass(frozen=True)
class _Values:
    """One line's values in the quote currency's minor units, exact."""

    goods: Fraction
    freight: Fraction  # its share of the order's freight
    duty: Fraction  # the duty-type charges on it so far


def _charge(
    market: MarketSet,
    request: QuoteRequest,
    charge: Charge,
    row: LandedRate,
    line: Line,
    lookup: VendorLookup | None,
    values: _Values,
    currency: Currency,
) -> Priced | AskVendor | VendorWaiting | LineRateProblem | FxMissing:
    """One charge on one line, exact, with where its rate came from."""
    match row.amount:
        case PerLine(amount=money) | PerParcel(amount=money):
            converted = convert_minor(market, money, currency)
            if isinstance(converted, FxMissing):
                return converted
            each = "per line" if isinstance(row.amount, PerLine) else "per parcel"
            how = f"{format_major(money)} {money.currency.value} {each}"
            return Priced(converted, how, f"landed_rates.csv line {row.line_no}")
        case Percent(base=base):
            pass
    if charge in (Charge.DUTY, Charge.IMPORT_TAX):
        result = line_rate(
            market, request.destination, charge, line, row, request.on, lookup
        )
        if not isinstance(result, Found):
            return result
        rate = result.rate
        source = _rate_source(result)
    else:
        rate = row.amount.rate
        source = f"landed_rates.csv line {row.line_no}"
    match base:
        case Base.GOODS:
            on = values.goods
        case Base.GOODS_PLUS_FREIGHT:
            on = values.goods + values.freight
        case Base.GOODS_FREIGHT_DUTY:
            on = values.goods + values.freight + values.duty
    how = f"{percent_text(rate)} of {BASE_WORDS[base]}"
    return Priced(on * rate, how, source)


def _rate_source(found: Found) -> str:
    match found.source:
        case FromHsRow(row=row):
            return f"hs_rates.csv line {row.line_no}"
        case FromBandRow(row=row):
            return f"landed_rates.csv line {row.line_no}"
        case FromVendor(vendor=vendor, answer=answer):
            return f"{vendor} said {answer.formula!r}"


def _line_goods(goods_parts: list[Part], lines: int, fee_total: int) -> list[Fraction]:
    """Each line's rounded price plus its share of the fees, by price
    (equal shares when every price is zero)."""
    prices: list[int] = []
    for part in goods_parts[:lines]:
        prices.append(part.amount.amount_minor)
    total = sum(prices)
    result: list[Fraction] = []
    for price in prices:
        if total:
            share = Fraction(price, total)
        else:
            share = Fraction(1, lines)
        result.append(price + fee_total * share)
    return result


def _add_once[T](items: list[T], item: T) -> None:
    if item not in items:
        items.append(item)


def _ship_part(ship: ShipPart, ship_source: str, currency: Currency) -> Part:
    source = ship_source
    if ship.line_no is not None:
        source = f"surcharges.csv line {ship.line_no}"
    return _part(
        ship.kind,
        ship.how if ship.kind == PartKind.FREIGHT else f"{ship.name}: {ship.how}",
        None,
        ship.exact,
        currency,
        SHIPPING_ROUNDING,
        None,
        source,
    )


def _part(
    kind: PartKind,
    label: str,
    line: int | None,
    exact: Fraction,
    currency: Currency,
    mode: RoundingMode,
    collector: Collector | None,
    source: str,
) -> Part:
    amount = Money(round_fraction(exact, mode), currency)
    return Part(
        kind=kind,
        label=label,
        line=line,
        exact=exact,
        amount=amount,
        mode=mode,
        collector=collector,
        source=source,
    )


def _rounded(parts: list[Part]) -> list[int]:
    amounts: list[int] = []
    for part in parts:
        amounts.append(part.amount.amount_minor)
    return amounts


def _currency(code: str | None) -> Currency | None:
    for currency in Currency:
        if currency.value == code:
            return currency
    return None
