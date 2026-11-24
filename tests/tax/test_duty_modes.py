"""Who decides a rate per HS code: our rows, the vendor, or our rows first
(docs/plan-quote.md §3, 4b-2), on small synthetic market sets and lookups
built in the test."""

import uuid
from datetime import date
from fractions import Fraction

from hypothesis import given, strategies

from mor.duties.port import percent_formula
from mor.duties.records import LookupState, StoredAnswer, VendorLookup
from mor.ids import DutyLookupId
from mor.markets.records import Charge, MarketSet
from mor.markets.rules import Open, country_rules
from mor.markets.source import SourceError, parse
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.tax.line_rates import (
    AskVendor,
    Found,
    FromHsRow,
    FromVendor,
    Line,
    LineRate,
    VendorNeedsPerson,
    VendorUnknownCode,
    VendorWaiting,
    line_rate,
)
from mor.tax.paths import BorderPaid, OrderValue, TaxPaths, tax_paths
from tests.markets.fixtures import SG_GST, TH_BORDER, market_texts


ON = date(2026, 11, 18)
TH_HS = "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"
LOOKUP_ID = DutyLookupId(uuid.UUID("01900000-0000-7000-8000-000000000001"))
T_SHIRT = Line("610910", "VN", frozenset())


def _market(duty_sources: str) -> MarketSet:
    market = parse(
        market_texts(SG_GST + TH_BORDER, hs_rows=TH_HS, duty_sources=duty_sources)
    )
    assert not isinstance(market, list), market
    return market


def _answer(
    charge: Charge, formula: str, rate: Fraction | None, note: str | None = None
) -> StoredAnswer:
    return StoredAnswer(
        charge=charge,
        formula=formula,
        rate=rate,
        note=note,
        hs_code_used="610910",
        own_rate=None,
        own_line_no=None,
    )


def _lookup(state: LookupState, *answers: StoredAnswer) -> VendorLookup:
    return VendorLookup(
        id=LOOKUP_ID, vendor="acme", state=state, calls=1, detail=None, answers=answers
    )


ANSWERED = _lookup(
    LookupState.ANSWERED,
    _answer(Charge.DUTY, "25%", Fraction(1, 4)),
    _answer(Charge.IMPORT_TAX, "7%", Fraction(7, 100)),
)


def _rate(
    market: MarketSet, charge: Charge, line: Line, lookup: VendorLookup | None
) -> LineRate:
    rules = country_rules(market, "TH", "apparel", ON)
    assert isinstance(rules, Open), rules
    paths = tax_paths(market, rules, OrderValue(Money(5000, Currency.USD), None), ON)
    assert isinstance(paths, TaxPaths), paths
    band = paths.duty if charge == Charge.DUTY else paths.indirect_tax
    assert isinstance(band, BorderPaid)
    return line_rate(market, "TH", charge, line, band.rate, ON, lookup)


def test_a_country_not_listed_uses_only_our_rows() -> None:
    found = _rate(_market(""), Charge.DUTY, T_SHIRT, ANSWERED)
    assert isinstance(found, Found) and isinstance(found.source, FromHsRow)
    assert found.rate == Fraction(30, 100)


def test_on_vendor_mode_the_vendors_answer_decides_over_our_row() -> None:
    market = _market("TH,vendor,acme,\n")
    found = _rate(market, Charge.DUTY, T_SHIRT, ANSWERED)
    assert isinstance(found, Found) and isinstance(found.source, FromVendor)
    assert found.rate == Fraction(1, 4)
    vat = _rate(market, Charge.IMPORT_TAX, T_SHIRT, ANSWERED)
    assert isinstance(vat, Found) and vat.rate == Fraction(7, 100)


def test_on_vendor_mode_a_combination_never_asked_asks_and_then_waits() -> None:
    market = _market("TH,vendor,acme,\n")
    assert _rate(market, Charge.DUTY, T_SHIRT, None) == AskVendor("acme")
    waiting = _rate(market, Charge.DUTY, T_SHIRT, _lookup(LookupState.REQUESTED))
    assert waiting == VendorWaiting(LOOKUP_ID)
    unknown = _rate(market, Charge.DUTY, T_SHIRT, _lookup(LookupState.UNKNOWN_CODE))
    assert unknown == VendorUnknownCode(LOOKUP_ID)


def test_own_first_uses_our_row_and_the_vendor_only_for_a_gap() -> None:
    market = _market("TH,own_first,acme,\n")
    ours = _rate(market, Charge.DUTY, T_SHIRT, ANSWERED)
    assert isinstance(ours, Found) and isinstance(ours.source, FromHsRow)
    shoes = Line("640399", "VN", frozenset())
    gap = _rate(market, Charge.DUTY, shoes, None)
    assert gap == AskVendor("acme")


def test_a_vendor_rate_by_weight_or_with_a_note_needs_a_person() -> None:
    market = _market("TH,vendor,acme,\n")
    specific = _lookup(LookupState.ANSWERED, _answer(Charge.DUTY, "2.00 USD/kg", None))
    noted = _lookup(
        LookupState.ANSWERED,
        _answer(Charge.DUTY, "0%", Fraction(0), "ATIGA preferential rate applied"),
    )
    by_weight = _rate(market, Charge.DUTY, T_SHIRT, specific)
    assert isinstance(by_weight, VendorNeedsPerson) and by_weight.reason == "formula"
    preference = _rate(market, Charge.DUTY, T_SHIRT, noted)
    assert isinstance(preference, VendorNeedsPerson) and preference.reason == "note"
    no_vat = _rate(market, Charge.IMPORT_TAX, T_SHIRT, specific)
    assert isinstance(no_vat, VendorNeedsPerson) and no_vat.reason == "no_charge"


def test_a_vendor_mode_must_name_its_vendor_and_own_only_none() -> None:
    for rows in ("TH,vendor,,\n", "TH,own_only,acme,\n", "TH,vendor,Acme Inc,\n"):
        errors = parse(market_texts(SG_GST + TH_BORDER, duty_sources=rows))
        assert isinstance(errors, list), rows
        assert isinstance(errors[0], SourceError) and errors[0].file == "duty_sources"


@given(
    whole=strategies.integers(min_value=0, max_value=1000),
    places=strategies.text(alphabet="0123456789", max_size=6),
)
def test_a_percentage_formula_reads_as_its_exact_rate(whole: int, places: str) -> None:
    written = f"{whole}.{places}" if places else str(whole)
    assert percent_formula(f"{written}%") == Fraction(written) / 100


def test_a_formula_that_is_not_a_plain_percentage_reads_as_none() -> None:
    for formula in ("2.00 USD/kg", "10% + 1.50 USD", "", "%", "ten%"):
        assert percent_formula(formula) is None, formula
