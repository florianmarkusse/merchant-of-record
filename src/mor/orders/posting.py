"""The capture's journal entry (docs/plan-payments.md §5; plan-architecture
§9), built from the stored quote option the buyer paid. Pure.

    quote currency:
      Dr psp_clearing:{psp account}    the option's total
      Cr tax_payable:{country}         taxes we collect ourselves
      Cr duties_payable:{courier}      border duties and taxes, duty-paid fee
      Cr shipping_payable:{courier}    freight, surcharges, fuel
      Cr psp_fee_collected             the estimated PSP fee
      Cr revenue_mor_margin            our margin and FX margin
      Cr merchant_payable:{merchant}   goods priced in the quote currency
      Cr fx_position                   goods priced in another currency
    each other currency the merchant priced lines in:
      Dr fx_position                   the merchant's prices, exact
      Cr merchant_payable:{merchant}   the merchant's prices, exact

The ledger holds whole minor units: it books each part's rounded amount,
which sum to the total; the sub-unit remainders stay on the quote's parts,
stored exactly (answer 6 of 19-Nov-2026). The merchant's own currency is
credited its price exactly, so the merchant is never short; the
conversion's rounding sits in `fx_position`.
"""

from datetime import datetime

from mor.audit import Actor
from mor.checkout.quotes import MadeQuote
from mor.checkout.records import SoldLine
from mor.ids import MerchantId, PaymentAttemptId, PspAccountId
from mor.ledger.account import (
    Account,
    DutiesPayable,
    FxPosition,
    MerchantPayable,
    PspClearing,
    PspFeeCollected,
    RevenueMorMargin,
    ShippingPayable,
    TaxPayable,
)
from mor.ledger.entry import JournalEntry, Line, build_entry
from mor.markets.records import Collector
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import Part
from mor.quote.groups import PartKind


def capture_key(attempt_id: PaymentAttemptId) -> str:
    return f"capture:{attempt_id}"


def _account_of(
    part: Part,
    lines: tuple[SoldLine, ...],
    quote_currency: Currency,
    merchant_id: MerchantId,
    country: str,
    courier: str,
) -> Account:
    match part.kind:
        case PartKind.GOODS:
            assert part.line is not None, "a goods part names its line"
            if lines[part.line].unit_price.currency == quote_currency:
                return MerchantPayable(merchant_id)
            return FxPosition()
        case PartKind.PSP_FEE:
            return PspFeeCollected()
        case PartKind.FX | PartKind.MOR_MARGIN:
            return RevenueMorMargin()
        case PartKind.FREIGHT | PartKind.SURCHARGE | PartKind.FUEL:
            return ShippingPayable(courier)
        case PartKind.DUTY_PAID_FEE:
            return DutiesPayable(courier)
        case (
            PartKind.DUTY
            | PartKind.ADDITIONAL_DUTY
            | PartKind.CUSTOMS_FEE
            | PartKind.IMPORT_TAX
            | PartKind.OTHER_TAX
        ):
            match part.collector:
                case Collector.BORDER:
                    return DutiesPayable(courier)
                case Collector.SELLER:
                    return TaxPayable(country)
                case None:
                    raise AssertionError(f"{part.kind} part without a collector")


def capture_entry(
    attempt_id: PaymentAttemptId,
    quote: MadeQuote,
    option_no: int,
    lines: tuple[SoldLine, ...],
    merchant_id: MerchantId,
    psp_account_id: PspAccountId,
    country: str,
    courier: str,
    captured_at: datetime,
    actor: Actor,
    now: datetime,
) -> JournalEntry:
    """Debit positive, credit negative, summed per account and currency;
    zero sums are left out (the ledger refuses a zero line)."""
    option = quote.options[option_no]
    currency = quote.currency
    sums: dict[tuple[Account, Currency], int] = {}

    def post(account: Account, amount: Money) -> None:
        key = (account, amount.currency)
        sums[key] = sums.get(key, 0) + amount.amount_minor

    post(
        PspClearing(psp_account_id),
        Money(option.total_minor, currency),
    )
    parts: list[Part] = []
    parts.extend(quote.goods)
    parts.extend(option.parts)
    for part in parts:
        account = _account_of(part, lines, currency, merchant_id, country, courier)
        post(account, Money(-part.amount.amount_minor, part.amount.currency))
    for line in lines:
        price = line.unit_price
        if price.currency == currency:
            continue
        exact = Money(price.amount_minor * line.quantity, price.currency)
        post(FxPosition(), exact)
        post(
            MerchantPayable(merchant_id),
            Money(-exact.amount_minor, exact.currency),
        )
    entry_lines: list[Line] = []
    for (account, line_currency), amount in sums.items():
        if amount != 0:
            entry_lines.append(Line(account, Money(amount, line_currency)))
    return build_entry(
        capture_key(attempt_id),
        f"capture of payment attempt {attempt_id}",
        captured_at,
        actor,
        entry_lines,
        now,
    )
