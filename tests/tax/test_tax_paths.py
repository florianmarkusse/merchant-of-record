"""The tax path rule, rates per HS code and exact FX (docs/plan-quote.md §3),
on small synthetic market sets. A registration here is made up inside the
test: real ones live in the registrations table."""

from datetime import date
from fractions import Fraction

from hypothesis import example, given
from hypothesis import strategies as st

from mor.markets.fx import FxMissing, convert_minor, rate
from mor.markets.records import Charge, MarketSet, PreferenceCondition
from mor.markets.rules import Open, country_rules
from mor.markets.source import parse
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.tax.line_rates import (
    Found,
    FromBandRow,
    FromHsRow,
    Line,
    RateNeeded,
    RateNotConfirmed,
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
from tests.markets.fixtures import FX_ROWS, SG_GST, TH_BORDER, market_texts


ON = date(2026, 11, 18)
REGISTERED_SG = "SG,OVR,M90000000X,2026-01-01,made up for the test\n"
TH_HS = (
    "TH,61,duty,,,,25,,secondary,WTO,https://example.test,,,\n"
    "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"
    "TH,6109,duty,VN,,,28,,secondary,WTO,https://example.test,,,\n"
    "TH,6109,duty,VN,ATIGA,certificate_of_origin direct_shipment,0,,"
    "secondary,ASEAN,https://example.test,,,\n"
)


def _market(**texts: str) -> MarketSet:
    landed = texts.pop("landed", SG_GST + TH_BORDER)
    market = parse(market_texts(landed, **texts))
    assert not isinstance(market, list), market
    return market


def _paths(market: MarketSet, country: str, order: OrderValue) -> TaxPaths:
    rules = country_rules(market, country, "apparel", ON)
    assert isinstance(rules, Open), rules
    paths = tax_paths(market, rules, order, ON)
    assert isinstance(paths, TaxPaths), paths
    return paths


def _sgd(minor: int) -> OrderValue:
    return OrderValue(Money(minor, Currency.SGD), None)


# --- the paths ----------------------------------------------------------------


def test_inside_a_seller_band_without_registration_nothing_is_due() -> None:
    paths = _paths(_market(), "SG", _sgd(25000))
    assert isinstance(paths.indirect_tax, NotDue)
    assert paths.indirect_tax.reason == "not_registered"
    assert not anything_at_border(paths)


def test_inside_a_seller_band_with_our_registration_we_collect() -> None:
    paths = _paths(_market(registrations=REGISTERED_SG), "SG", _sgd(25000))
    assert isinstance(paths.indirect_tax, SellerCollected)
    assert paths.indirect_tax.registration.number == "M90000000X"


def test_a_registration_applies_only_from_its_date() -> None:
    later = "SG,OVR,M90000000X,2027-01-01,made up for the test\n"
    paths = _paths(_market(registrations=later), "SG", _sgd(25000))
    assert isinstance(paths.indirect_tax, NotDue)


def test_the_limit_itself_is_inside_an_up_to_band_and_a_cent_more_is_not() -> None:
    at_limit = _paths(_market(), "SG", _sgd(40000))
    above = _paths(_market(), "SG", _sgd(40001))
    assert isinstance(at_limit.indirect_tax, NotDue)
    assert at_limit.indirect_tax.reason == "not_registered"  # inside the band
    assert isinstance(above.indirect_tax, BorderPaid)
    assert anything_at_border(above)


def test_vat_and_duty_are_decided_separately() -> None:
    paths = _paths(_market(), "TH", OrderValue(Money(1000, Currency.USD), None))
    assert isinstance(paths.indirect_tax, BorderPaid)
    assert isinstance(paths.duty, BorderPaid)
    sg = _paths(_market(), "SG", _sgd(60000))
    assert isinstance(sg.indirect_tax, BorderPaid)
    assert isinstance(sg.duty, NotDue)


def test_a_limit_on_goods_plus_freight_waits_for_the_freight() -> None:
    landed = SG_GST.replace("SGD,goods,", "SGD,goods_plus_freight,")
    market = _market(landed=landed + TH_BORDER)
    rules = country_rules(market, "SG", "apparel", ON)
    assert isinstance(rules, Open)
    assert tax_paths(market, rules, _sgd(30000), ON) == NeedsFreight(2)
    with_freight = OrderValue(Money(30000, Currency.SGD), Money(15000, Currency.SGD))
    paths = tax_paths(market, rules, with_freight, ON)
    assert isinstance(paths, TaxPaths)
    assert isinstance(paths.indirect_tax, BorderPaid)  # 450 > 400


def test_a_value_in_another_currency_is_compared_after_exact_conversion() -> None:
    # 400 SGD is 400 / 1.4309 * 1.1165 = 312.11... USD.
    below = OrderValue(Money(31211, Currency.USD), None)
    above = OrderValue(Money(31212, Currency.USD), None)
    assert isinstance(_paths(_market(), "SG", below).indirect_tax, NotDue)
    assert isinstance(_paths(_market(), "SG", above).indirect_tax, BorderPaid)


def test_an_unconfirmed_exchange_rate_is_not_used() -> None:
    weak = FX_ROWS.replace(
        "EUR,USD,1.1165,2026-11-06,verified", "EUR,USD,1.1165,2026-11-06,estimate"
    )
    market = _market(fx_rows=weak)
    rules = country_rules(market, "SG", "apparel", ON)
    assert isinstance(rules, Open)
    order = OrderValue(Money(1000, Currency.USD), None)
    assert tax_paths(market, rules, order, ON) == FxMissing(Currency.USD, Currency.SGD)


@given(minor=st.integers(min_value=0, max_value=10**9))
@example(minor=40000)  # the limit itself: inside the up_to band
@example(minor=40001)
def test_every_sg_order_takes_exactly_the_band_its_value_falls_in(minor: int) -> None:
    paths = _paths(_market(), "SG", _sgd(minor))
    if minor <= 40000:
        assert isinstance(paths.indirect_tax, NotDue)
        assert paths.indirect_tax.reason == "not_registered"
    else:
        assert isinstance(paths.indirect_tax, BorderPaid)


# --- rates per HS code ----------------------------------------------------------


def _duty_rate(hs_code: str | None, origin: str, met: frozenset[PreferenceCondition]):
    market = _market(hs_rows=TH_HS)
    paths = _paths(market, "TH", OrderValue(Money(5000, Currency.USD), None))
    assert isinstance(paths.duty, BorderPaid)
    line = Line(hs_code, origin, met)
    return line_rate(market, "TH", Charge.DUTY, line, paths.duty.rate, ON, None)


def test_the_longest_prefix_and_the_lines_origin_win() -> None:
    from_china = _duty_rate("610910", "CN", frozenset())
    from_vietnam = _duty_rate("610910", "VN", frozenset())
    other_knit = _duty_rate("611020", "CN", frozenset())
    assert isinstance(from_china, Found) and from_china.rate == Fraction(30, 100)
    assert isinstance(from_vietnam, Found) and from_vietnam.rate == Fraction(28, 100)
    assert isinstance(other_knit, Found) and other_knit.rate == Fraction(25, 100)


def test_an_agreement_applies_only_when_every_condition_is_met() -> None:
    both = frozenset(
        {PreferenceCondition.CERTIFICATE_OF_ORIGIN, PreferenceCondition.DIRECT_SHIPMENT}
    )
    one = frozenset({PreferenceCondition.CERTIFICATE_OF_ORIGIN})
    met = _duty_rate("610910", "VN", both)
    half = _duty_rate("610910", "VN", one)
    assert isinstance(met, Found) and met.rate == 0 and met.missed == ()
    assert isinstance(half, Found) and half.rate == Fraction(28, 100)
    assert half.missed[0].missing == {PreferenceCondition.DIRECT_SHIPMENT}


def test_an_unconfirmed_agreement_rate_is_reported_not_applied() -> None:
    weak = TH_HS.replace("0,,secondary,ASEAN", "0,,unverified,ASEAN")
    market = _market(hs_rows=weak)
    paths = _paths(market, "TH", OrderValue(Money(5000, Currency.USD), None))
    assert isinstance(paths.duty, BorderPaid)
    everything = frozenset(PreferenceCondition)
    line = Line("610910", "VN", everything)
    found = line_rate(market, "TH", Charge.DUTY, line, paths.duty.rate, ON, None)
    assert isinstance(found, Found) and found.rate == Fraction(28, 100)
    assert found.missed[0].unconfirmed


def test_duty_without_an_hs_rate_is_needed_never_a_category_guess() -> None:
    assert _duty_rate(None, "VN", frozenset()) == RateNeeded(Charge.DUTY, "no_hs_code")
    assert _duty_rate("640399", "VN", frozenset()) == RateNeeded(Charge.DUTY, "no_rate")


def test_vat_without_an_hs_rate_takes_the_band_rows_rate() -> None:
    market = _market()
    paths = _paths(market, "TH", OrderValue(Money(5000, Currency.USD), None))
    assert isinstance(paths.indirect_tax, BorderPaid)
    line = Line("610910", "VN", frozenset())
    found = line_rate(
        market, "TH", Charge.IMPORT_TAX, line, paths.indirect_tax.rate, ON, None
    )
    assert isinstance(found, Found)
    assert found.rate == Fraction(7, 100)
    assert isinstance(found.source, FromBandRow)


def test_an_unconfirmed_standard_hs_rate_is_not_used() -> None:
    weak = TH_HS.replace("6109,duty,,,,30,,secondary", "6109,duty,,,,30,,estimate")
    market = _market(hs_rows=weak)
    paths = _paths(market, "TH", OrderValue(Money(5000, Currency.USD), None))
    assert isinstance(paths.duty, BorderPaid)
    line = Line("610910", "CN", frozenset())
    assert line_rate(
        market, "TH", Charge.DUTY, line, paths.duty.rate, ON, None
    ) == RateNotConfirmed(3)


def test_a_found_rate_names_the_row_it_came_from() -> None:
    found = _duty_rate("610910", "CN", frozenset())
    assert isinstance(found, Found) and isinstance(found.source, FromHsRow)
    assert found.source.row.line_no == 3


# --- exchange rates -------------------------------------------------------------


def test_a_rate_is_found_through_a_chain_of_rates() -> None:
    market = _market()
    # VND -> USD (SBV) -> EUR -> SGD (ECB).
    expected = Fraction(1, 25600) / Fraction("1.1165") * Fraction("1.4309")
    assert rate(market, Currency.VND, Currency.SGD) == expected
    assert rate(market, Currency.SGD, Currency.VND) == 1 / expected


@given(minor=st.integers(min_value=0, max_value=10**12))
def test_converting_there_and_back_gives_the_amount_exactly(minor: int) -> None:
    market = _market()
    there = convert_minor(market, Money(minor, Currency.VND), Currency.SGD)
    assert isinstance(there, Fraction)
    sgd_to_vnd = rate(market, Currency.SGD, Currency.VND)
    assert isinstance(sgd_to_vnd, Fraction)
    assert there / 100 * sgd_to_vnd == minor
