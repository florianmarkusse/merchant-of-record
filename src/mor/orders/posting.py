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

import uuid
from datetime import datetime
from typing import assert_never

from mor.audit import Actor
from mor.checkout.quotes import MadeQuote
from mor.checkout.records import SoldLine
from mor.ledger.account import Account, AccountKind
from mor.ledger.entry import JournalEntry, Line, build_entry
from mor.markets.records import Collector
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.quote.build import Part
from mor.quote.groups import PartKind


def capture_key(attempt_id: uuid.UUID) -> str:
    return f"capture:{attempt_id}"


def _account_of(
    part: Part,
    lines: tuple[SoldLine, ...],
    quote_currency: Currency,
    merchant_id: uuid.UUID,
    country: str,
    courier: str,
) -> Account:
    match part.kind:
        case PartKind.GOODS:
            assert part.line is not None, "a goods part names its line"
            if lines[part.line].unit_price.currency == quote_currency:
                return Account(AccountKind.MERCHANT_PAYABLE, str(merchant_id))
            return Account(AccountKind.FX_POSITION)
        case PartKind.PSP_FEE:
            return Account(AccountKind.PSP_FEE_COLLECTED)
        case PartKind.FX | PartKind.MOR_MARGIN:
            return Account(AccountKind.REVENUE_MOR_MARGIN)
        case PartKind.FREIGHT | PartKind.SURCHARGE | PartKind.FUEL:
            return Account(AccountKind.SHIPPING_PAYABLE, courier)
        case PartKind.DUTY_PAID_FEE:
            return Account(AccountKind.DUTIES_PAYABLE, courier)
        case (
            PartKind.DUTY
            | PartKind.ADDITIONAL_DUTY
            | PartKind.CUSTOMS_FEE
            | PartKind.IMPORT_TAX
            | PartKind.OTHER_TAX
        ):
            match part.collector:
                case Collector.BORDER:
                    return Account(AccountKind.DUTIES_PAYABLE, courier)
                case Collector.SELLER:
                    return Account(AccountKind.TAX_PAYABLE, country)
                case None:
                    raise AssertionError(f"{part.kind} part without a collector")
                case _:
                    assert_never(part.collector)
        case _:
            assert_never(part.kind)


def capture_entry(
    attempt_id: uuid.UUID,
    quote: MadeQuote,
    option_no: int,
    lines: tuple[SoldLine, ...],
    merchant_id: uuid.UUID,
    psp_account_id: uuid.UUID,
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
        Account(AccountKind.PSP_CLEARING, str(psp_account_id)),
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
        post(Account(AccountKind.FX_POSITION), exact)
        post(
            Account(AccountKind.MERCHANT_PAYABLE, str(merchant_id)),
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
