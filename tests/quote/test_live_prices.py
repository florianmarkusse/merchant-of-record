"""The quote with a service priced live by its carrier (docs/plan-quote.md
§4, 4c-2), on the synthetic set of `tests/quote/fixtures.py` with the
express service routed `carrier_api`: the quote waits for a usable price,
then charges every part the carrier gave, and keeps the duty-paid fee,
the categories and the weight limit our own."""

import uuid
from datetime import UTC, date, datetime, timedelta

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
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import (
    InputsMissing,
    PartKind,
    Quote,
    QuoteLine,
    QuoteRequest,
    QuoteResult,
    ShippingOption,
    build_quote,
)
from mor.quote.shipping import NotOffered, NotOfferedReason
from tests.quote.fixtures import live_market


ON = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
BOX = Parcel(weight_g=1000, length_mm=200, width_mm=150, height_mm=100)
TO_SG = PriceQuestion(
    carrier="carrier", service="exp", origin="VN", destination="SG", parcel=BOX
)
# The same parts our exp_card charges for this parcel, so the totals of
# test_build_quote.py hold.
CARD_LIKE = StoredPrice(
    total=Money(4650, Currency.SGD),
    parts=(
        PricePart(PricePartKind.FREIGHT, "freight", 3000),
        PricePart(PricePartKind.SURCHARGE, "demand", 100),
        PricePart(PricePartKind.FUEL, "fuel", 1550),
    ),
    carrier_valid_until=None,
    own=None,
)


def _request(minor: int, parcel: Parcel = BOX) -> QuoteRequest:
    line = QuoteLine(
        category="apparel",
        hs_code="610910",
        origin="VN",
        quantity=1,
        unit_price=Money(minor, Currency.SGD),
    )
    return QuoteRequest(destination="SG", lines=(line,), parcel=parcel, on=ON, now=NOW)


def _held(
    state: PriceState = PriceState.ANSWERED,
    price: StoredPrice | None = CARD_LIKE,
    usable_until: datetime = NOW + timedelta(hours=1),
) -> PriceHeld:
    finished = FinishedLookup(
        id=uuid.uuid7(),
        state=state,
        calls=1,
        detail=None,
        finished_at=NOW - timedelta(hours=1),
        usable_until=usable_until,
        price=price,
    )
    return PriceHeld(finished, None)


def _option(result: QuoteResult, key: str) -> ShippingOption:
    assert isinstance(result, Quote), result
    for option in result.options:
        if option.key == key:
            return option
    raise AssertionError(f"no option {key}")


def test_a_live_route_waits_for_the_carrier_then_quotes_with_its_parts() -> None:
    market = live_market()
    request = _request(50000)
    assert build_quote(market, request, {}, {}) == InputsMissing(
        asks=(), waiting=(), price_asks=(TO_SG,), price_waiting=()
    )
    open_id = uuid.uuid7()
    waiting = {TO_SG: PriceHeld(None, open_id)}
    assert build_quote(market, request, {}, waiting) == InputsMissing(
        asks=(), waiting=(), price_asks=(), price_waiting=(open_id,)
    )

    held = _held()
    assert held.latest is not None
    option = _option(build_quote(market, request, {}, {TO_SG: held}), "live:exp")
    assert option.rate_card is None
    assert option.live is not None and option.live.lookup_id == held.latest.id
    amounts: dict[PartKind, int] = {}
    for part in option.parts:
        amounts[part.kind] = amounts.get(part.kind, 0) + part.amount.amount_minor
    # As exp_card quotes it (test_build_quote.py): the carrier's parts, then
    # our duty-paid fee's minimum and GST on goods and that freight.
    assert amounts == {
        PartKind.FREIGHT: 3000,
        PartKind.SURCHARGE: 100,
        PartKind.FUEL: 1550,
        PartKind.DUTY_PAID_FEE: 1000,
        PartKind.IMPORT_TAX: 5278,
    }
    assert option.total == Money(64928, Currency.SGD)
    for part in option.parts:
        if part.kind == PartKind.DUTY_PAID_FEE:
            assert part.source == "surcharges.csv line 4"
        if part.kind == PartKind.FREIGHT:
            assert part.source == "carrier rate call"


def test_a_price_is_never_used_from_its_usable_until_on() -> None:
    market = live_market()
    held = _held(usable_until=NOW)
    assert build_quote(market, _request(50000), {}, {TO_SG: held}) == InputsMissing(
        asks=(), waiting=(), price_asks=(TO_SG,), price_waiting=()
    )


def test_a_carrier_price_in_another_currency_is_converted_and_rounded_up() -> None:
    in_vnd = StoredPrice(
        total=Money(500_001, Currency.VND),
        parts=(PricePart(PricePartKind.FREIGHT, "freight", 500_001),),
        carrier_valid_until=None,
        own=None,
    )
    held = _held(price=in_vnd)
    option = _option(
        build_quote(live_market(), _request(10000), {}, {TO_SG: held}), "live:exp"
    )
    [freight] = option.parts
    assert freight.amount.currency == Currency.SGD
    assert freight.amount.amount_minor >= freight.exact > 0
    assert freight.amount.amount_minor - freight.exact < 1


def test_not_served_or_an_unread_answer_leaves_the_option_out() -> None:
    cases: tuple[tuple[PriceState, NotOfferedReason], ...] = (
        (PriceState.NOT_SERVED, NotOfferedReason.CARRIER_NOT_SERVED),
        (PriceState.NEEDS_PERSON, NotOfferedReason.CARRIER_NEEDS_PERSON),
    )
    for state, reason in cases:
        held = _held(state=state, price=None)
        result = build_quote(live_market(), _request(10000), {}, {TO_SG: held})
        assert isinstance(result, Quote), result
        assert [option.key for option in result.options] == ["eco_card"]
        assert NotOffered("live:exp", reason, "") in result.not_offered


def test_a_parcel_over_the_services_limit_is_never_asked_about() -> None:
    heavy = Parcel(
        weight_g=31_000, length_mm=100, width_mm=100, height_mm=100
    )  # exp carries up to 30 kg
    result = build_quote(live_market(), _request(10000, heavy), {}, {})
    assert not isinstance(result, InputsMissing), result
