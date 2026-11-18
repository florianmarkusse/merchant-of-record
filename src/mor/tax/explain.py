"""The tax decision for one order, for a person: a short summary (who sets
the rates, what is charged at the border and what that rules out), one row
per charge (who collects it, its rate, who said so, what our own data
says), and the data rows that decided it (docs/plan-quote.md §5b, the staff
quote page). Pure: the same decisions as `tax_paths` and `line_rate`, in
words."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal, assert_never

from mor.duties.records import VendorLookup
from mor.markets.changes import BASE_WORDS, percent_text, value_text
from mor.markets.fx import FxMissing
from mor.markets.records import (
    Band,
    Charge,
    Collector,
    DutyMode,
    LandedRate,
    MarketSet,
    Percent,
)
from mor.markets.rules import (
    NotListed,
    NotOpen,
    Open,
    RatesMissing,
    RulesNotConfirmed,
    country_rules,
)
from mor.money.amount import Money
from mor.tax.line_rates import (
    AskVendor,
    Found,
    FromBandRow,
    FromHsRow,
    FromVendor,
    Line,
    MissedPreference,
    RateNeeded,
    RateNotConfirmed,
    VendorNeedsPerson,
    VendorUnknownCode,
    VendorWaiting,
    duty_source_for,
    line_rate,
)
from mor.tax.paths import (
    BorderPaid,
    NeedsFreight,
    NotDue,
    OrderValue,
    SellerCollected,
    anything_at_border,
    tax_paths,
)


@dataclass(frozen=True)
class OrderCheck:
    country: str
    category: str
    goods: Money
    freight: Money | None
    hs_code: str | None
    origin: str


CHARGE_WORDS = {
    Charge.DUTY: "Duty",
    Charge.ADDITIONAL_DUTY: "Additional duty",
    Charge.FEE: "Customs fee",
    Charge.IMPORT_TAX: "VAT / GST",
    Charge.OTHER_TAX: "Other tax",
}


# The same, inside a sentence.
CHARGE_IN_TEXT = {
    Charge.DUTY: "duty",
    Charge.ADDITIONAL_DUTY: "additional duty",
    Charge.FEE: "a customs fee",
    Charge.IMPORT_TAX: "VAT/GST",
    Charge.OTHER_TAX: "another tax",
}


@dataclass(frozen=True)
class OwnData:
    """What our own rows say beside a vendor's rate. `differs`: a person
    should look."""

    text: str
    differs: bool


@dataclass(frozen=True)
class ChargeRow:
    charge: Charge
    collected: str  # "at the border, on every order", "not due: …"
    rate: str | None  # "25% of the price with fees + freight"; None: none yet
    says: str | None  # who said so
    own: OwnData | None  # None: the rate is ours, or there is none
    problem: str | None  # why there is no usable rate yet
    missed: tuple[str, ...]  # trade agreements not applied, and why


@dataclass(frozen=True)
class DataRow:
    """One row of our data, or a vendor's answer, that a decision used."""

    where: str  # "landed_rates.csv line 7", "zonos"
    says: str
    research: str | None  # how well the row is researched; None for a vendor


@dataclass(frozen=True)
class Explained:
    summary: tuple[str, ...]  # sentences, most important first
    charges: tuple[ChargeRow, ...]
    rows: tuple[DataRow, ...]  # the data rows that decided it, for checking
    # The vendor that decides and has not been asked about this order's
    # combination: the page offers to ask it.
    ask_vendor: str | None


def explain(
    market: MarketSet, check: OrderCheck, on: date, lookup: VendorLookup | None
) -> Explained:
    """`lookup` is what we hold from the destination's vendor for the
    order's (destination, origin, HS code), if any."""
    name = _country_name(market, check.country)
    rules = country_rules(market, check.country, check.category, on)
    match rules:
        case NotListed():
            return _only(f"{check.country} is not in the countries file.")
        case NotOpen(status=status, note=note):
            return _only(f"{name} is closed to us ({status.value}): {note}")
        case RulesNotConfirmed(line_nos=line_nos):
            lines = ", ".join(str(line_no) for line_no in line_nos)
            return _only(
                f"{name}'s tax rules are not confirmed yet"
                f" (landed_rates.csv lines {lines})."
            )
        case RatesMissing(category=category):
            return _only(f"We have no import tax recorded for {category} in {name}.")
        case Open():
            pass
    paths = tax_paths(market, rules, OrderValue(check.goods, check.freight), on)
    match paths:
        case NeedsFreight(line_no=line_no):
            return _only(
                f"{name}'s threshold counts the price plus freight"
                f" (landed_rates.csv line {line_no}): add a parcel so the"
                " freight is known."
            )
        case FxMissing(base=base, quote=quote):
            return _only(
                f"No confirmed exchange rate from {base.value} to {quote.value}."
            )
        case _:
            pass
    line = Line(check.hs_code, check.origin, frozenset())
    asks: list[str] = []
    rows: list[DataRow] = []
    charges: list[ChargeRow] = []
    match paths.indirect_tax:
        case SellerCollected(rate=row, registration=registration):
            collected = (
                f"by us, under {registration.scheme} {registration.number},"
                f" {_band_text(row)}"
            )
            charges.append(
                _charge(
                    market,
                    check,
                    Charge.IMPORT_TAX,
                    collected,
                    row,
                    line,
                    on,
                    lookup,
                    asks,
                    rows,
                )
            )
        case BorderPaid(rate=row):
            collected = f"at the border, {_band_text(row)}"
            charges.append(
                _charge(
                    market,
                    check,
                    Charge.IMPORT_TAX,
                    collected,
                    row,
                    line,
                    on,
                    lookup,
                    asks,
                    rows,
                )
            )
        case NotDue(reason="not_registered", rate=row):
            if row is not None:
                rows.append(_row_text(row))
            charges.append(
                _not_due(
                    Charge.IMPORT_TAX,
                    "not due: ours to collect on this order, but we are not"
                    " registered there yet",
                )
            )
        case NotDue():
            charges.append(
                _not_due(Charge.IMPORT_TAX, "not due: no band covers this value")
            )
    match paths.duty:
        case BorderPaid(rate=row):
            collected = f"at the border, {_band_text(row)}"
            if isinstance(row.amount, Percent):
                charges.append(
                    _charge(
                        market,
                        check,
                        Charge.DUTY,
                        collected,
                        row,
                        line,
                        on,
                        lookup,
                        asks,
                        rows,
                    )
                )
            else:
                rows.append(_row_text(row))
                charges.append(
                    ChargeRow(
                        Charge.DUTY,
                        collected,
                        value_text(row.amount),
                        "the country's flat amount",
                        None,
                        None,
                        (),
                    )
                )
        case NotDue():
            charges.append(_not_due(Charge.DUTY, "not due: no band covers this value"))
    for row in paths.other_border:
        rows.append(_row_text(row))
        charges.append(
            ChargeRow(
                row.charge,
                f"at the border, {_band_text(row)}",
                value_text(row.amount),
                "the country's rule",
                None,
                None,
                (),
            )
        )
    summary: list[str] = []
    at_border: list[str] = []
    for charge in charges:
        if charge.collected.startswith("at the border"):
            at_border.append(CHARGE_IN_TEXT[charge.charge])
    if anything_at_border(paths):
        them = "them" if len(at_border) > 1 else "it"
        summary.append(
            f"{name} charges {' and '.join(at_border)} at the border on this"
            f" order. The carrier pays {them} for us, so only duty-paid"
            " services can carry the order; the others are listed as not"
            " offered."
        )
    else:
        summary.append(
            f"Nothing is charged at the border in {name} on this order, so any"
            " service on the lane can carry it."
        )
    summary.append(_mode_text(market, check.country))
    for charge in charges:
        if charge.own is not None and charge.own.differs:
            summary.append(
                f"Check the {CHARGE_IN_TEXT[charge.charge]} rate: our own"
                " data differs from the vendor's."
            )
    ask = asks[0] if asks else None
    return Explained(tuple(summary), tuple(charges), tuple(rows), ask)


def _only(sentence: str) -> Explained:
    return Explained((sentence,), (), (), None)


def _not_due(charge: Charge, why: str) -> ChargeRow:
    return ChargeRow(charge, why, None, None, None, None, ())


def _country_name(market: MarketSet, iso2: str) -> str:
    for country in market.countries:
        if country.iso2 == iso2:
            return country.name
    return iso2


def _band_text(row: LandedRate) -> str:
    """Which orders a row covers: "on every order", "on orders up to …"."""
    match row.band:
        case Band.ALWAYS:
            return "on every order"
        case Band.UP_TO:
            return f"on orders up to {value_text(row.limit)}"
        case Band.ABOVE:
            return f"on orders above {value_text(row.limit)}"


def _mode_text(market: MarketSet, country: str) -> str:
    source = duty_source_for(market, country)
    match source.mode:
        case DutyMode.OWN_ONLY:
            return "Rates per HS code come from our own research rows."
        case DutyMode.OWN_FIRST:
            return (
                "Rates per HS code come from our own research rows, and from"
                f" {source.vendor} where we have none."
            )
        case DutyMode.VENDOR:
            return (
                f"Rates per HS code come from {source.vendor}; our own rows are"
                " kept to compare with."
            )


def _row_text(row: LandedRate) -> DataRow:
    place = row.scope.country or row.scope.group
    match row.amount:
        case Percent(rate=rate, base=base):
            amount = f"{percent_text(rate)} of {BASE_WORDS[base]}"
        case _:
            amount = value_text(row.amount)
    collector = "at the border" if row.collector == Collector.BORDER else "by us"
    return DataRow(
        f"landed_rates.csv line {row.line_no}",
        f"{place} {CHARGE_IN_TEXT[row.charge]}: {amount}, {collector},"
        f" {_band_text(row)}",
        row.status.value,
    )


def _charge(
    market: MarketSet,
    check: OrderCheck,
    charge: Charge,
    collected: str,
    band_row: LandedRate,
    line: Line,
    on: date,
    lookup: VendorLookup | None,
    asks: list[str],
    rows: list[DataRow],
) -> ChargeRow:
    """One charge with a percentage rate per HS code. A vendor still to be
    asked is appended to `asks`, each data row used to `rows`."""
    rows.append(_row_text(band_row))
    assert isinstance(band_row.amount, Percent)
    base = BASE_WORDS[band_row.amount.base]
    product = f"HS {check.hs_code} made in {check.origin}"
    result = line_rate(market, check.country, charge, line, band_row, on, lookup)
    match result:
        case Found(rate=rate, source=source, missed=missed):
            rate_text = f"{percent_text(rate)} of {base}"
            missed_text = _missed_text(missed, base, rows)
            match source:
                case FromHsRow(row=row):
                    rows.append(
                        DataRow(
                            f"hs_rates.csv line {row.line_no}",
                            f"HS {row.hs_prefix}: {percent_text(row.rate)}",
                            row.status.value,
                        )
                    )
                    return ChargeRow(
                        charge,
                        collected,
                        rate_text,
                        f"our own row for HS {row.hs_prefix}",
                        None,
                        None,
                        missed_text,
                    )
                case FromBandRow():
                    return ChargeRow(
                        charge,
                        collected,
                        rate_text,
                        "the country's standard rate",
                        None,
                        None,
                        missed_text,
                    )
                case FromVendor(vendor=vendor, answer=answer):
                    rows.append(
                        DataRow(
                            vendor,
                            f"{CHARGE_IN_TEXT[charge]} for HS"
                            f" {answer.hs_code_used}: {answer.formula!r}",
                            None,
                        )
                    )
                    own = _own_data(answer.rate, answer.own_rate, answer.own_line_no)
                    return ChargeRow(
                        charge,
                        collected,
                        rate_text,
                        f"{vendor}, for {product}",
                        own,
                        None,
                        missed_text,
                    )
        case RateNeeded(reason=reason):
            if reason == "no_hs_code":
                problem = "enter the HS code"
            else:
                problem = (
                    f"we have no rate for {product}; a quote is refused until"
                    " one exists"
                )
            return _no_rate(charge, collected, base, problem)
        case RateNotConfirmed(line_no=line_no):
            return _no_rate(
                charge,
                collected,
                base,
                f"our rate is not confirmed yet (hs_rates.csv line {line_no})",
            )
        case AskVendor(vendor=vendor):
            if vendor not in asks:
                asks.append(vendor)
            return _no_rate(
                charge,
                collected,
                base,
                f"{vendor} has not been asked about {product} yet",
            )
        case VendorWaiting():
            return _no_rate(
                charge,
                collected,
                base,
                f"asked about {product}; waiting for the answer",
            )
        case VendorUnknownCode():
            return _no_rate(
                charge,
                collected,
                base,
                f"the vendor does not know {product}; a quote is refused",
            )
        case VendorNeedsPerson(reason=reason, detail=detail):
            return _no_rate(charge, collected, base, _needs_person_text(reason, detail))


def _no_rate(charge: Charge, collected: str, base: str, problem: str) -> ChargeRow:
    return ChargeRow(charge, collected, f"? of {base}", None, None, problem, ())


def _missed_text(
    missed: tuple[MissedPreference, ...], base: str, rows: list[DataRow]
) -> tuple[str, ...]:
    said: list[str] = []
    for miss in missed:
        assert miss.rate.preference is not None
        reasons: list[str] = []
        if miss.missing:
            needs: list[str] = []
            for condition in miss.missing:
                needs.append(condition.value.replace("_", " "))
            reasons.append(f"needs {' and '.join(sorted(needs))}")
        if miss.unconfirmed:
            reasons.append(f"its rate is {miss.rate.status.value}")
        said.append(
            f"{miss.rate.preference.agreement} {percent_text(miss.rate.rate)}"
            f" not applied: {'; '.join(reasons)}"
        )
        rows.append(
            DataRow(
                f"hs_rates.csv line {miss.rate.line_no}",
                f"HS {miss.rate.hs_prefix} under"
                f" {miss.rate.preference.agreement}: {percent_text(miss.rate.rate)}",
                miss.rate.status.value,
            )
        )
    return tuple(said)


def _needs_person_text(
    reason: Literal["undecodable", "no_charge", "formula", "note"], detail: str
) -> str:
    match reason:
        case "undecodable":
            return f"a person must read the vendor's answer ({detail})"
        case "formula":
            return f"the vendor's formula {detail!r} is not a plain percentage"
        case "note":
            return (
                f"the vendor noted {detail!r}, which may be a preference applied"
                " without our conditions; not used"
            )
        case "no_charge":
            return f"the vendor gave no {detail} rate"
        case _:
            assert_never(reason)


def _own_data(
    vendor_rate: Fraction | None, own_rate: Fraction | None, own_line_no: int | None
) -> OwnData:
    if own_rate is None or own_line_no is None:
        return OwnData("no row to compare", False)
    own = percent_text(own_rate)
    if vendor_rate == own_rate:
        return OwnData(f"agrees: {own} (hs_rates.csv line {own_line_no})", False)
    return OwnData(f"{own}: differs (hs_rates.csv line {own_line_no})", True)
