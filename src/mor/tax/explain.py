"""The tax decision for one order, in words a person reads: which path each
charge takes, the row that decided it and why, and the rate per HS code
with any agreement missed, or the vendor's answer beside our own row
(docs/plan-quote.md §3, the staff page). Pure."""

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal, assert_never

from mor.duties.records import VendorLookup
from mor.markets.changes import value_text
from mor.markets.fx import FxMissing
from mor.markets.records import (
    Base,
    Charge,
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


@dataclass(frozen=True)
class Explained:
    lines: list[str]
    # The vendor that decides and has not been asked about this order's
    # combination: the page offers to ask it.
    ask_vendor: str | None


def explain(
    market: MarketSet, check: OrderCheck, on: date, lookup: VendorLookup | None
) -> Explained:
    """`lookup` is what we hold from the destination's vendor for the
    order's (destination, origin, HS code), if any."""
    rules = country_rules(market, check.country, check.category, on)
    match rules:
        case NotListed():
            return Explained([f"{check.country} is not in the countries file."], None)
        case NotOpen(status=status, note=note):
            return Explained([f"Closed ({status.value}): {note}"], None)
        case RulesNotConfirmed(line_nos=line_nos):
            lines = ", ".join(str(line_no) for line_no in line_nos)
            return Explained(
                [f"Closed: rules not confirmed (landed_rates.csv lines {lines})."],
                None,
            )
        case RatesMissing(category=category):
            return Explained([f"Closed: no import tax recorded for {category}."], None)
        case Open():
            pass
    paths = tax_paths(market, rules, OrderValue(check.goods, check.freight), on)
    match paths:
        case NeedsFreight(line_no=line_no):
            return Explained(
                [
                    f"Needs the freight: landed_rates.csv line {line_no} measures"
                    " goods plus freight. Enter a freight amount."
                ],
                None,
            )
        case FxMissing(base=base, quote=quote):
            return Explained(
                [f"No confirmed exchange rate from {base.value} to {quote.value}."],
                None,
            )
        case _:
            pass
    line = Line(check.hs_code, check.origin, frozenset())
    asks: list[str] = []
    said: list[str] = [_mode_text(market, check.country)]
    match paths.indirect_tax:
        case SellerCollected(rate=row, registration=registration):
            said.append(
                f"VAT/GST: collected by us under {registration.scheme}"
                f" {registration.number}; decided by {_row(row)}."
            )
            said.extend(
                _rate(market, check, Charge.IMPORT_TAX, line, row, on, lookup, asks)
            )
        case BorderPaid(rate=row):
            said.append(f"VAT/GST: charged at the border; decided by {_row(row)}.")
            said.extend(
                _rate(market, check, Charge.IMPORT_TAX, line, row, on, lookup, asks)
            )
        case NotDue(reason="not_registered", rate=row):
            said.append(
                "VAT/GST: not due. The order is inside a seller band"
                f" ({_row(row) if row else 'no row'}), and we hold no"
                " registration there yet."
            )
        case NotDue():
            said.append("VAT/GST: not due: no band holds this value.")
    match paths.duty:
        case BorderPaid(rate=row):
            said.append(f"Duty: charged at the border; decided by {_row(row)}.")
            if isinstance(row.amount, Percent):
                said.extend(
                    _rate(market, check, Charge.DUTY, line, row, on, lookup, asks)
                )
            else:
                said.append(f"  Duty is {value_text(row.amount)}: no HS rate needed.")
        case NotDue():
            said.append("Duty: not due: no band holds this value.")
    for row in paths.other_border:
        said.append(f"Also at the border: {_row(row)}.")
    if anything_at_border(paths):
        said.append("Only duty-paid services may carry this order.")
    else:
        said.append("Nothing is due at the border: any service on the lane may.")
    ask = asks[0] if asks else None
    return Explained(said, ask)


def _mode_text(market: MarketSet, country: str) -> str:
    source = duty_source_for(market, country)
    match source.mode:
        case DutyMode.OWN_ONLY:
            return "Rates per HS code: our own rows only (own_only)."
        case DutyMode.OWN_FIRST:
            return (
                f"Rates per HS code: our own rows first; {source.vendor} fills"
                " the gaps (own_first)."
            )
        case DutyMode.VENDOR:
            return (
                f"Rates per HS code: {source.vendor} decides; our own rows are"
                " compared with it (vendor)."
            )


def _fraction_text(rate: Fraction) -> str:
    return value_text(Percent(rate, Base.GOODS)).removesuffix(" of goods")


def _row(row: LandedRate) -> str:
    band = row.band.value
    if row.limit is not None:
        band = f"{band} {value_text(row.limit)}"
    place = row.scope.country or row.scope.group
    return (
        f"landed_rates.csv line {row.line_no} ({place}, {value_text(row.amount)},"
        f" {row.collector.value}, {band}, {row.status.value})"
    )


def _rate(
    market: MarketSet,
    check: OrderCheck,
    charge: Charge,
    line: Line,
    band_row: LandedRate,
    on: date,
    lookup: VendorLookup | None,
    asks: list[str],
) -> list[str]:
    """What the rate per HS code says, in lines; a vendor still to be asked
    is appended to `asks`."""
    result = line_rate(market, check.country, charge, line, band_row, on, lookup)
    combination = f"HS {check.hs_code} from {check.origin}"
    match result:
        case Found(rate=rate, source=source, missed=missed):
            percent = value_text(Percent(rate, _base(band_row)))
            said: list[str] = []
            match source:
                case FromHsRow(row=row):
                    said.append(
                        f"  Rate {percent}: hs_rates.csv line {row.line_no}"
                        f" (HS {row.hs_prefix}, {row.status.value})."
                    )
                case FromBandRow():
                    said.append(f"  Rate {percent}: the band row's own rate.")
                case FromVendor(vendor=vendor, answer=answer):
                    said.append(
                        f"  Rate {percent}: {vendor} said {answer.formula!r} for"
                        f" HS {answer.hs_code_used}."
                    )
                    compared = _comparison(
                        answer.rate, answer.own_rate, answer.own_line_no
                    )
                    said.append(f"  {compared}")
            for miss in missed:
                assert miss.rate.preference is not None
                reasons: list[str] = []
                if miss.missing:
                    needs: list[str] = []
                    for condition in miss.missing:
                        needs.append(condition.value)
                    reasons.append(f"needs {', '.join(sorted(needs))}")
                if miss.unconfirmed:
                    reasons.append(f"its rate is {miss.rate.status.value}")
                said.append(
                    f"  Not applied: {miss.rate.preference.agreement}"
                    f" {value_text(Percent(miss.rate.rate, _base(band_row)))}"
                    f" (hs_rates.csv line {miss.rate.line_no}): {'; '.join(reasons)}."
                )
            return said
        case RateNeeded(reason=reason):
            if reason == "no_hs_code":
                return [f"  {charge.value} rate needed: enter the HS code."]
            return [
                f"  {charge.value} rate needed: no HS rate for {combination};"
                " a quote is refused until one exists."
            ]
        case RateNotConfirmed(line_no=line_no):
            return [f"  Rate not confirmed: hs_rates.csv line {line_no}."]
        case AskVendor(vendor=vendor):
            if vendor not in asks:
                asks.append(vendor)
            return [
                f"  {charge.value} rate: {vendor} has not been asked about"
                f" {combination}."
            ]
        case VendorWaiting():
            return [
                f"  {charge.value} rate: asked about {combination}; waiting for"
                " the answer."
            ]
        case VendorUnknownCode():
            return [
                f"  {charge.value} rate: the vendor does not know {combination};"
                " a quote is refused."
            ]
        case VendorNeedsPerson(reason=reason, detail=detail):
            why = _needs_person_text(reason, detail)
            return [f"  {charge.value} rate needs a person: {why}"]


def _needs_person_text(
    reason: Literal["undecodable", "no_charge", "formula", "note"], detail: str
) -> str:
    match reason:
        case "undecodable":
            return f"the vendor's answer could not be read ({detail})."
        case "formula":
            return f"the vendor's formula {detail!r} is not a plain percentage."
        case "note":
            return (
                f"the vendor noted {detail!r}, which may be a preference applied"
                " without our conditions; not used."
            )
        case "no_charge":
            return f"the vendor gave no {detail} rate."
        case _:
            assert_never(reason)


def _comparison(
    vendor_rate: Fraction | None, own_rate: Fraction | None, own_line_no: int | None
) -> str:
    if own_rate is None or own_line_no is None:
        return "Our own rows had none to compare."
    own = _fraction_text(own_rate)
    if vendor_rate == own_rate:
        return f"Our own row agrees ({own}, hs_rates.csv line {own_line_no})."
    return f"Our own row differs: {own} (hs_rates.csv line {own_line_no})."


def _base(row: LandedRate) -> Base:
    assert isinstance(row.amount, Percent)
    return row.amount.base
