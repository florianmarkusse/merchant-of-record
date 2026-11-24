"""The quote for one order (docs/plan-quote.md §4, 4c-1), on the synthetic
shipping set of `tests/quote/fixtures.py`: one option per routed card,
each with its full total; the parts sum to it, every part is rounded by
its named mode, and a card is left out with its reason."""

import uuid
from datetime import UTC, date, datetime
from fractions import Fraction

from mor.couriers.records import Parcel
from mor.duties.port import RateQuestion
from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.ids import DutyLookupId
from mor.markets.records import Charge, Collector, FeeComponent
from mor.markets.rules import NotOpen
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import (
    CountryClosed,
    InputsMissing,
    LineRateRefused,
    NoFeeRule,
    PartKind,
    Quote,
    QuoteLine,
    QuoteRequest,
    QuoteResult,
    VendorAsk,
    build_quote,
)
from mor.quote.shipping import NotOffered, NotOfferedReason
from mor.tax.line_rates import RateNeeded
from tests.quote.fixtures import quote_market


ON = date(2026, 11, 18)
NOW = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
BOX = Parcel(
    weight_g=1000, length_mm=200, width_mm=150, height_mm=100
)  # 1 kg; 0.6 kg by volume
T_SHIRT = RateQuestion("TH", "VN", "610910")


def _line(
    minor: int, currency: Currency = Currency.SGD, hs: str | None = "610910"
) -> QuoteLine:
    return QuoteLine(
        category="apparel",
        hs_code=hs,
        origin="VN",
        quantity=1,
        unit_price=Money(minor, currency),
    )


def _quote(result: QuoteResult) -> Quote:
    assert isinstance(result, Quote), result
    return result


def _amounts(quote: Quote, rate_card: str) -> dict[str, int]:
    """The option's parts and the goods parts by kind, in minor units."""
    amounts: dict[str, int] = {}
    for part in quote.goods:
        amounts[part.kind.value] = (
            amounts.get(part.kind.value, 0) + part.amount.amount_minor
        )
    for option in quote.options:
        if option.key != rate_card:
            continue
        for part in option.parts:
            key = part.kind.value
            amounts[key] = amounts.get(key, 0) + part.amount.amount_minor
        amounts["total"] = option.total.amount_minor
    return amounts


def test_an_order_above_the_border_limit_adds_tax_and_the_duty_paid_fee() -> None:
    request = QuoteRequest(
        destination="SG", lines=(_line(50000),), parcel=BOX, on=ON, now=NOW
    )
    quote = _quote(build_quote(quote_market(), request, {}, {}))
    # Worked by hand: SGD 500.00; fees 3% and 5%; 1 kg at SGD 30.00,
    # demand SGD 1.00, fuel 50% of 31.00; GST 9% of (540.00 + 46.50) is
    # 52.785, half to even 52.78; the duty-paid fee's 2% is 1.06, so its
    # minimum of SGD 10.00 applies.
    assert _amounts(quote, "exp_card") == {
        "goods": 50000,
        "psp_fee": 1500,
        "fx": 0,
        "mor_margin": 2500,
        "freight": 3000,
        "surcharge": 100,
        "fuel": 1550,
        "duty_paid_fee": 1000,
        "import_tax": 5278,
        "total": 64928,
    }
    assert NotOffered(
        "eco_card", NotOfferedReason.NOT_DUTY_PAID, "something is due at the border"
    ) in (quote.not_offered)
    [option] = quote.options
    assert option.transit_days == (1, 3)
    assert option.fuel_stale_since == date(2026, 11, 15)


def test_below_the_limit_nothing_is_due_and_every_card_is_offered_cheapest_first() -> (
    None
):
    request = QuoteRequest(
        destination="SG", lines=(_line(10000),), parcel=BOX, on=ON, now=NOW
    )
    quote = _quote(build_quote(quote_market(), request, {}, {}))
    cards: list[str] = []
    for option in quote.options:
        cards.append(option.key)
        for part in option.parts:
            assert part.kind not in (PartKind.IMPORT_TAX, PartKind.DUTY_PAID_FEE)
    assert cards == ["eco_card", "exp_card"]
    assert quote.options[0].total.amount_minor < quote.options[1].total.amount_minor


def test_duty_and_tax_are_shared_over_the_lines_by_their_value() -> None:
    lines = (_line(20000), _line(60000))
    quote = _quote(
        build_quote(
            quote_market(),
            QuoteRequest(destination="SG", lines=lines, parcel=BOX, on=ON, now=NOW),
            {},
            {},
        )
    )
    [option] = quote.options
    taxed: list[Fraction] = []
    for part in option.parts:
        if part.kind == PartKind.IMPORT_TAX:
            assert part.collector == Collector.BORDER
            taxed.append(part.exact)
    # 9% of goods with fees (864.00) plus freight (46.50), split 1:3.
    assert taxed == [
        Fraction(9, 100) * Fraction(91050, 4),
        Fraction(9, 100) * Fraction(91050 * 3, 4),
    ]


def test_a_card_offered_in_vendor_mode_waits_for_the_vendors_rate() -> None:
    market = quote_market(duty_sources="TH,vendor,acme,\n")
    request = QuoteRequest(
        destination="TH", lines=(_line(5000, Currency.USD),), parcel=BOX, on=ON, now=NOW
    )
    assert build_quote(market, request, {}, {}) == InputsMissing(
        asks=(VendorAsk("acme", T_SHIRT),), waiting=()
    )
    asked = VendorLookup(
        id=DutyLookupId(uuid.uuid7()),
        vendor="acme",
        state=LookupState.REQUESTED,
        calls=1,
        detail=None,
        answers=(),
    )
    assert build_quote(market, request, {T_SHIRT: asked}, {}) == InputsMissing(
        asks=(), waiting=(asked.id,)
    )
    answered = VendorLookup(
        id=asked.id,
        vendor="acme",
        state=LookupState.ANSWERED,
        calls=1,
        detail=None,
        answers=(
            StoredAnswer(
                charge=Charge.DUTY,
                formula="25%",
                rate=Fraction(1, 4),
                note=None,
                hs_code_used="610910",
                own_rate=None,
                own_line_no=None,
            ),
            StoredAnswer(
                charge=Charge.IMPORT_TAX,
                formula="7%",
                rate=Fraction(7, 100),
                note=None,
                hs_code_used="610910",
                own_rate=None,
                own_line_no=None,
            ),
        ),
    )
    quote = _quote(build_quote(market, request, {T_SHIRT: answered}, {}))
    sources: set[str] = set()
    for part in quote.options[0].parts:
        if part.kind in (PartKind.DUTY, PartKind.IMPORT_TAX):
            sources.add(part.source)
    assert sources == {"acme said '25%'", "acme said '7%'"}


def test_a_line_without_the_hs_code_its_duty_needs_is_refused() -> None:
    request = QuoteRequest(
        destination="TH",
        lines=(_line(5000, Currency.USD, hs=None),),
        parcel=BOX,
        on=ON,
        now=NOW,
    )
    result = build_quote(quote_market(), request, {}, {})
    assert result == LineRateRefused(
        0, Charge.DUTY, RateNeeded(Charge.DUTY, "no_hs_code")
    )


def test_a_closed_country_or_a_missing_fee_rule_is_refused() -> None:
    closed = build_quote(
        quote_market(),
        QuoteRequest(
            destination="DE", lines=(_line(5000),), parcel=BOX, on=ON, now=NOW
        ),
        {},
        {},
    )
    assert isinstance(closed, CountryClosed) and isinstance(closed.closed, NotOpen)
    no_margin = quote_market(
        {
            "fees": "psp_fee,,3,,,2026-01-01,placeholder,made up,\n"
            "fx,,0,,,2026-01-01,agreed,made up,\n"
        }
    )
    request = QuoteRequest(
        destination="SG", lines=(_line(5000),), parcel=BOX, on=ON, now=NOW
    )
    assert build_quote(no_margin, request, {}, {}) == NoFeeRule(FeeComponent.MOR_MARGIN)


def test_a_quote_names_the_placeholder_fee_rules_it_used() -> None:
    quote = _quote(
        build_quote(
            quote_market(),
            QuoteRequest(
                destination="SG", lines=(_line(5000),), parcel=BOX, on=ON, now=NOW
            ),
            {},
            {},
        )
    )
    assert quote.placeholders == (
        "fees.csv line 2 (psp_fee)",
        "fees.csv line 4 (mor_margin)",
    )
