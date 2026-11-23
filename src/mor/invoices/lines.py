"""An invoice's lines from the stored quote option it was paid for. Pure.

The lines are the four groups the buyer saw (plan-architecture §8), as
whole minor units that sum to the option's total:

- goods: one per order line, the merchant's price plus its share of our
  fees, split by `allocate` weighted by each line's rounded price, so the
  goods lines sum to the "Items" the buyer saw;
- shipping: one line, every shipping part of the option;
- tax and border: each duty and tax part on its own line, by who collects
  it, with how the quote reached it (its rate inside that text).
"""

from typing import assert_never

from mor.checkout.quotes import MadeQuote
from mor.checkout.records import SoldLine
from mor.invoices.records import InvoiceLine, InvoiceSection
from mor.markets.records import Collector
from mor.money.amount import Money
from mor.money.rounding import allocate
from mor.quote.groups import PartKind, group_of


CHARGE_WORDS: dict[PartKind, str] = {
    PartKind.DUTY: "Import duty",
    PartKind.ADDITIONAL_DUTY: "Additional import duty",
    PartKind.CUSTOMS_FEE: "Customs fee",
    PartKind.IMPORT_TAX: "Import tax",
    PartKind.OTHER_TAX: "Tax",
}


def _section(collector: Collector | None) -> InvoiceSection:
    match collector:
        case Collector.SELLER:
            return InvoiceSection.TAX
        case Collector.BORDER:
            return InvoiceSection.BORDER
        case None:
            raise AssertionError("a duty or tax part with no collector")
        case _:
            assert_never(collector)


def invoice_lines(
    quote: MadeQuote, option_no: int, sold: tuple[SoldLine, ...]
) -> tuple[InvoiceLine, ...]:
    option = quote.options[option_no]
    items = 0
    weights = [0] * len(sold)
    for part in quote.goods:
        items += part.amount.amount_minor
        if part.kind == PartKind.GOODS:
            assert part.line is not None, "a goods part names its line"
            weights[part.line] += part.amount.amount_minor
    if sum(weights) == 0:
        weights = [1] * len(sold)  # every price zero: equal shares
    shares = allocate(Money(items, quote.currency), weights)
    lines: list[InvoiceLine] = []
    for line, share in zip(sold, shares, strict=True):
        lines.append(
            InvoiceLine(
                section=InvoiceSection.GOODS,
                description=line.name,
                quantity=line.quantity,
                amount_minor=share.amount_minor,
                rule_text=None,
                source=None,
            )
        )
    shipping = 0
    charges: list[InvoiceLine] = []
    for part in option.parts:
        match group_of(part.kind):
            case "shipping":
                shipping += part.amount.amount_minor
            case "duties_and_taxes":
                words = CHARGE_WORDS[part.kind]
                if part.line is not None:
                    words = f"{words}: {sold[part.line].name}"
                charges.append(
                    InvoiceLine(
                        section=_section(part.collector),
                        description=words,
                        quantity=None,
                        amount_minor=part.amount.amount_minor,
                        rule_text=part.label,
                        source=part.source,
                    )
                )
            case "goods" | "fees":
                raise AssertionError(f"an option holds a {part.kind} part")
    lines.append(
        InvoiceLine(
            section=InvoiceSection.SHIPPING,
            description=f"Shipping: {option.service_name}",
            quantity=None,
            amount_minor=shipping,
            rule_text=None,
            source=None,
        )
    )
    for section in (InvoiceSection.TAX, InvoiceSection.BORDER):
        for charge in charges:
            if charge.section == section:
                lines.append(charge)
    return tuple(lines)
