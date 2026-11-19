"""Properties of `build_quote` for any order (docs/plan-quote.md §4, 4c-1;
conventions: fuzz every pure function with a property worth stating), on
the synthetic set and on the research files themselves:

- the parts sum to each option's total, in the quote's currency;
- every part is its exact value rounded by its named mode, so the
  rounding remainder is the difference and nothing else is lost;
- the merchant's price is never short: each goods part is at least the
  exact conversion of what the merchant asked;
- duties and taxes are never negative, the duty-paid fee is charged
  exactly when something is paid at the border, and options come
  cheapest first;
- with a service priced by its carrier (4c-2), each part the carrier gave
  is charged at its exact conversion, rounded up.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from mor.couriers.records import (
    FinishedLookup,
    Parcel,
    PriceHeld,
    PricePart,
    PricePartKind,
    PriceQuestion,
    PriceState,
    StoredPrice,
)
from mor.duties.port import RateQuestion
from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.markets.fx import convert_minor
from mor.markets.load import MARKETS_DIR
from mor.markets.records import Charge, Collector, MarketSet
from mor.markets.source import parse, read_texts
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.money.rounding import RoundingMode, round_fraction
from mor.quote.build import PartKind, Quote, QuoteLine, QuoteRequest, build_quote
from tests.quote.fixtures import live_market, quote_market


SYNTHETIC = quote_market()
LIVE = live_market()
NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
_texts = read_texts(MARKETS_DIR)
assert not isinstance(_texts, list)
_research = parse(_texts)
assert not isinstance(_research, list)
RESEARCH: MarketSet = _research

HS_CODES = (None, "610910", "640399", "6910")
PRICED_IN = (Currency.SGD, Currency.USD, Currency.VND, Currency.EUR, Currency.THB)


def _lookups(destinations: tuple[str, ...]) -> dict[RateQuestion, VendorLookup]:
    """An answered lookup for every combination the strategies make, so
    vendor-mode countries quote."""
    lookups: dict[RateQuestion, VendorLookup] = {}
    for destination in destinations:
        for hs_code in HS_CODES:
            if hs_code is None:
                continue
            question = RateQuestion(destination, "VN", hs_code)
            lookups[question] = VendorLookup(
                uuid.uuid7(),
                "zonos",
                LookupState.ANSWERED,
                1,
                None,
                (
                    StoredAnswer(
                        Charge.DUTY, "12%", Fraction(12, 100), None, hs_code, None, None
                    ),
                    StoredAnswer(
                        Charge.IMPORT_TAX,
                        "8%",
                        Fraction(8, 100),
                        None,
                        hs_code,
                        None,
                        None,
                    ),
                ),
            )
    return lookups


# The countries open for apparel in the research files today.
RESEARCH_PLACES = ("SG", "TH")
RESEARCH_LOOKUPS = _lookups(RESEARCH_PLACES)

lines = st.lists(
    st.builds(
        QuoteLine,
        category=st.just("apparel"),
        hs_code=st.sampled_from(HS_CODES),
        origin=st.just("VN"),
        quantity=st.integers(min_value=1, max_value=20),
        unit_price=st.builds(
            Money,
            amount_minor=st.integers(min_value=0, max_value=50_000_000),
            currency=st.sampled_from(PRICED_IN),
        ),
    ),
    min_size=1,
    max_size=4,
)
# Mostly parcels the cards price (up to 2 kg in the synthetic set, 30 kg
# in the research cards), now and then one no card takes.
parcels = st.builds(
    Parcel,
    weight_g=st.one_of(
        st.integers(min_value=1, max_value=2_000),
        st.integers(min_value=1, max_value=40_000),
    ),
    length_mm=st.integers(min_value=10, max_value=400),
    width_mm=st.integers(min_value=10, max_value=300),
    height_mm=st.integers(min_value=5, max_value=200),
)
# From the research fee rules' start; the synthetic cards start 01-Jan-2026.
days = st.dates(min_value=date(2026, 11, 1), max_value=date(2027, 6, 30))
days_start = date(2026, 11, 18)


def _check(market: MarketSet, request: QuoteRequest, quote: Quote) -> None:
    for index, part in enumerate(quote.goods):
        assert part.amount.currency == quote.currency
        assert part.amount.amount_minor == round_fraction(part.exact, part.mode)
        if part.kind == PartKind.GOODS:
            line = request.lines[index]
            price = Money(
                line.unit_price.amount_minor * line.quantity, line.unit_price.currency
            )
            exact = convert_minor(market, price, quote.currency)
            assert isinstance(exact, Fraction)
            assert part.amount.amount_minor >= exact
    goods_total = 0
    for part in quote.goods:
        goods_total += part.amount.amount_minor
    previous: int | None = None
    for option in quote.options:
        total = goods_total
        at_border = False
        duty_paid = False
        for part in option.parts:
            assert part.amount.currency == quote.currency
            assert part.amount.amount_minor == round_fraction(part.exact, part.mode)
            assert part.exact >= 0
            total += part.amount.amount_minor
            if part.collector == Collector.BORDER:
                at_border = True
            if part.kind == PartKind.DUTY_PAID_FEE:
                duty_paid = True
        assert option.total == Money(total, quote.currency)
        if duty_paid:
            assert at_border
        if previous is not None:
            assert option.total.amount_minor >= previous
        previous = option.total.amount_minor


@given(
    destination=st.sampled_from(("SG", "SG", "TH", "TH", "DE")),
    order=lines,
    parcel=parcels,
    on=days,
)
def test_any_order_on_the_synthetic_set_quotes_consistently_or_is_refused(
    destination: str, order: list[QuoteLine], parcel: Parcel, on: date
) -> None:
    request = QuoteRequest(destination, tuple(order), parcel, on, NOW)
    result = build_quote(SYNTHETIC, request, _lookups((destination,)), {})
    if isinstance(result, Quote):
        _check(SYNTHETIC, request, result)


@given(
    destination=st.sampled_from(RESEARCH_PLACES),
    order=lines,
    parcel=parcels,
    on=days,
)
def test_any_order_on_the_research_files_quotes_consistently_or_is_refused(
    destination: str, order: list[QuoteLine], parcel: Parcel, on: date
) -> None:
    request = QuoteRequest(destination, tuple(order), parcel, on, NOW)
    result = build_quote(RESEARCH, request, RESEARCH_LOOKUPS, {})
    if isinstance(result, Quote):
        _check(RESEARCH, request, result)


carrier_parts = st.lists(
    st.builds(
        PricePart,
        kind=st.sampled_from(PricePartKind),
        name=st.sampled_from(("freight", "fuel", "peak")),
        amount_minor=st.integers(min_value=0, max_value=5_000_000),
    ),
    min_size=1,
    max_size=4,
)


@given(
    destination=st.sampled_from(("SG", "TH")),
    order=lines,
    parcel=parcels,
    parts=carrier_parts,
    currency=st.sampled_from((Currency.SGD, Currency.VND, Currency.USD)),
)
def test_any_carrier_price_quotes_consistently_with_each_part_charged(
    destination: str,
    order: list[QuoteLine],
    parcel: Parcel,
    parts: list[PricePart],
    currency: Currency,
) -> None:
    total = 0
    for part in parts:
        total += part.amount_minor
    price = StoredPrice(Money(total, currency), tuple(parts), None, None)
    held = PriceHeld(
        FinishedLookup(
            uuid.uuid7(),
            PriceState.ANSWERED,
            1,
            None,
            NOW,
            NOW + timedelta(hours=1),
            price,
        ),
        None,
    )
    question = PriceQuestion("carrier", "exp", "VN", destination, parcel)
    request = QuoteRequest(destination, tuple(order), parcel, days_start, NOW)
    result = build_quote(LIVE, request, _lookups((destination,)), {question: held})
    if not isinstance(result, Quote):
        return
    _check(LIVE, request, result)
    for option in result.options:
        if option.live is None:
            continue
        for index, part in enumerate(parts):
            charged = option.parts[index]
            exact = convert_minor(
                LIVE, Money(part.amount_minor, currency), result.currency
            )
            assert charged.exact == exact
            assert charged.mode == RoundingMode.CEILING
