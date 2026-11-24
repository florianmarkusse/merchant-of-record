"""Shipping prices from our own rate tables (docs/plan-quote.md §4, 4c-1),
on the synthetic set of `tests/quote/fixtures.py`: billed weight, fuel on
freight and surcharges, and every reason a card is not offered."""

from datetime import UTC, date, datetime
from fractions import Fraction

from mor.couriers.records import Parcel
from mor.markets.records import MarketSet
from mor.money.currency import Currency
from mor.quote.groups import PartKind
from mor.quote.shipping import (
    NotOffered,
    NotOfferedReason,
    Offer,
    PriceMissing,
    ShipPartKind,
    offers,
)
from tests.quote.fixtures import SHIPPING, quote_market


ON = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


def _offers(
    market: MarketSet, parcel: Parcel, on: date = ON, category: str = "apparel"
) -> dict[str, Offer | NotOffered]:
    found: dict[str, Offer | NotOffered] = {}
    for offer in offers(
        market,
        "SG",
        frozenset({category}),
        parcel,
        1,
        Fraction(0),
        Currency.SGD,
        on,
        {},
        NOW,
    ):
        assert not isinstance(offer, PriceMissing), "no live route in the set"
        code = offer.key if isinstance(offer, NotOffered) else offer.route.key
        found[code] = offer
    return found


def _express(market: MarketSet, parcel: Parcel, on: date = ON) -> Offer:
    offer = _offers(market, parcel, on)["exp_card"]
    assert isinstance(offer, Offer), offer
    return offer


def _part(offer: Offer, kind: ShipPartKind) -> Fraction:
    for part in offer.parts:
        if part.kind == kind:
            return part.exact
    raise AssertionError(f"no {kind} part")


def test_a_bulky_box_is_billed_by_volume_at_the_next_weight_break() -> None:
    # 400 g in 30 x 25 x 10 cm: 7,500 cm³ / 5,000 = 1.5 kg, billed as 2 kg.
    offer = _express(
        quote_market(), Parcel(weight_g=400, length_mm=300, width_mm=250, height_mm=100)
    )
    assert offer.chargeable_kg == Fraction(3, 2) and offer.billed_kg == 2
    assert _part(offer, PartKind.FREIGHT) == 4500


def test_fuel_is_charged_on_the_freight_and_the_surcharges_it_applies_to() -> None:
    offer = _express(
        quote_market(),
        Parcel(weight_g=1000, length_mm=100, width_mm=100, height_mm=100),
    )
    assert _part(offer, PartKind.FREIGHT) == 3000
    assert _part(offer, PartKind.SURCHARGE) == 100  # demand, SGD 1.00 per kg
    assert _part(offer, PartKind.FUEL) == Fraction(3100, 2)
    names: list[str] = []
    for part in offer.parts:
        names.append(part.name)
    assert "remote_area" not in names  # only charged on an event


def test_a_fuel_rate_past_its_week_is_still_used_and_flagged() -> None:
    parcel = Parcel(weight_g=1000, length_mm=100, width_mm=100, height_mm=100)
    assert _express(quote_market(), parcel, ON).fuel_stale_since == date(2026, 11, 15)
    assert _express(quote_market(), parcel, date(2026, 11, 14)).fuel_stale_since is None


def test_a_card_is_left_out_with_the_reason() -> None:
    small = Parcel(weight_g=500, length_mm=100, width_mm=100, height_mm=100)
    heavy = _offers(
        quote_market(),
        Parcel(weight_g=3000, length_mm=100, width_mm=100, height_mm=100),
    )["eco_card"]
    assert (
        isinstance(heavy, NotOffered) and heavy.reason == NotOfferedReason.OVER_WEIGHT
    )

    banned = quote_market(
        {"service_restrictions": "eco,apparel,prohibited,verified,,,\n"}
    )
    restricted = _offers(banned, small)["eco_card"]
    assert isinstance(restricted, NotOffered)
    assert restricted.reason == NotOfferedReason.CATEGORY_RESTRICTED

    awaiting = SHIPPING["surcharges"].replace(
        "demand,per_kg,,100,,SGD,always,SG TH,yes,,,verified",
        "demand,per_kg,,100,,SGD,always,SG TH,yes,,,awaiting_answer",
    )
    unconfirmed = _offers(quote_market({"surcharges": awaiting}), small)["exp_card"]
    assert isinstance(unconfirmed, NotOffered)
    assert unconfirmed.reason == NotOfferedReason.SURCHARGE_NOT_CONFIRMED

    early = _offers(quote_market(), small, date(2025, 12, 31))["exp_card"]
    assert (
        isinstance(early, NotOffered)
        and early.reason == NotOfferedReason.CARD_NOT_IN_FORCE
    )

    ranged = SHIPPING["rates"].replace(
        "eco_card,1,0.5,100000,,,", "eco_card,1,0.5,100000,120000,,"
    )
    estimate = _offers(quote_market({"rates": ranged}), small)["eco_card"]
    assert (
        isinstance(estimate, NotOffered)
        and estimate.reason == NotOfferedReason.PRICE_RANGE
    )
