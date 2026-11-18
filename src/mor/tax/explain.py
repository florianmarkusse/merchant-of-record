"""The tax decision for one order, in words a person reads: which path each
charge takes, the row that decided it and why, and the rate per HS code
with any agreement missed (docs/plan-quote.md §3, the staff page). Pure."""

from dataclasses import dataclass
from datetime import date

from mor.markets.changes import value_text
from mor.markets.fx import FxMissing
from mor.markets.records import Base, Charge, LandedRate, MarketSet, Percent
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


def explain(market: MarketSet, check: OrderCheck, on: date) -> list[str]:
    rules = country_rules(market, check.country, check.category, on)
    match rules:
        case NotListed():
            return [f"{check.country} is not in the countries file."]
        case NotOpen(status=status, note=note):
            return [f"Closed ({status.value}): {note}"]
        case RulesNotConfirmed(line_nos=line_nos):
            lines = ", ".join(str(line_no) for line_no in line_nos)
            return [f"Closed: rules not confirmed (landed_rates.csv lines {lines})."]
        case RatesMissing(category=category):
            return [f"Closed: no import tax recorded for {category}."]
        case Open():
            pass
    paths = tax_paths(market, rules, OrderValue(check.goods, check.freight), on)
    match paths:
        case NeedsFreight(line_no=line_no):
            return [
                f"Needs the freight: landed_rates.csv line {line_no} measures"
                " goods plus freight. Enter a freight amount."
            ]
        case FxMissing(base=base, quote=quote):
            return [f"No confirmed exchange rate from {base.value} to {quote.value}."]
        case _:
            pass
    line = Line(check.hs_code, check.origin, frozenset())
    said: list[str] = []
    match paths.indirect_tax:
        case SellerCollected(rate=row, registration=registration):
            said.append(
                f"VAT/GST: collected by us under {registration.scheme}"
                f" {registration.number}; decided by {_row(row)}."
            )
            said.extend(_rate(market, check, Charge.IMPORT_TAX, line, row, on))
        case BorderPaid(rate=row):
            said.append(f"VAT/GST: charged at the border; decided by {_row(row)}.")
            said.extend(_rate(market, check, Charge.IMPORT_TAX, line, row, on))
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
                said.extend(_rate(market, check, Charge.DUTY, line, row, on))
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
    return said


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
) -> list[str]:
    result = line_rate(market, check.country, charge, line, band_row, on)
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
                f"  {charge.value} rate needed: no HS rate for {check.hs_code} from"
                f" {check.origin}; a quote is refused until one exists."
            ]
        case RateNotConfirmed(line_no=line_no):
            return [f"  Rate not confirmed: hs_rates.csv line {line_no}."]


def _base(row: LandedRate) -> Base:
    assert isinstance(row.amount, Percent)
    return row.amount.base
