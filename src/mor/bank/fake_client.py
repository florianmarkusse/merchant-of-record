"""Client for the fake bank's API (mor.fakes.bank), in the shape of a bank's
transactions API (ABN AMRO's Business Account Insight): our records out,
never our database.

Amounts are signed decimal strings in major units, as a bank shows them.
One that is not a whole number in our minor units is `UndecodableBody`:
a bank books what it holds, so it is not ours to round.
"""

import asyncio
from datetime import date
from fractions import Fraction

import httpx2
import msgspec

from mor.bank.port import BankAccount, BankTransaction, PageResult, TransactionsPage
from mor.ids import BankTransactionId
from mor.money.amount import Money
from mor.money.currency import Currency, exponent
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


class _Transaction(msgspec.Struct, frozen=True, kw_only=True, rename="camel"):
    transaction_id: str
    amount: str
    currency: str
    balance_after_mutation: str
    description_lines: list[str]
    counter_party_name: str
    book_date: date


class _PageKey(msgspec.Struct, frozen=True, rename="camel"):
    """Only what the reader needs: where to go on from."""

    next_page_key: str | None
    has_more: bool


class _Page(msgspec.Struct, frozen=True, rename="camel"):
    transactions: list[_Transaction]
    next_page_key: str | None
    has_more: bool


async def list_transactions(
    http: httpx2.AsyncClient, account: BankAccount, page_key: str | None
) -> PageResult:
    """The transactions booked after the position `page_key` (none: from
    the first), oldest first, one page."""
    params: dict[str, str] = {}
    if page_key is not None:
        params["pageKey"] = page_key
    try:
        async with asyncio.timeout(account.timeout.total_seconds()):
            sent = await http.get(account.base_url + "/transactions", params=params)
    except httpx2.ConnectError as error:
        return Unavailable(repr(error))
    except (TimeoutError, httpx2.TransportError) as error:
        return OutcomeUnknown(repr(error))
    if sent.status_code >= 500:
        return OutcomeUnknown(f"status {sent.status_code}")
    if sent.status_code != 200:
        return UndecodableBody(f"status {sent.status_code}")
    try:
        page = msgspec.json.decode(sent.content, type=_PageKey)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    return TransactionsPage(sent.content, page.next_page_key, page.has_more)


def _money(text: str, currency: Currency) -> Money | UndecodableBody:
    try:
        major = Fraction(text)
    except ValueError, ZeroDivisionError:
        return UndecodableBody(f"amount {text!r}")
    minor = major * 10 ** exponent(currency)
    if minor.denominator != 1:
        return UndecodableBody(f"{text} {currency} is not whole in our units")
    return Money(int(minor), currency)


def decode_transactions(
    account: BankAccount, body: bytes
) -> tuple[BankTransaction, ...] | UndecodableBody:
    try:
        page = msgspec.json.decode(body, type=_Page)
    except msgspec.DecodeError as error:
        return UndecodableBody(str(error))
    found: list[BankTransaction] = []
    for raw in page.transactions:
        if raw.currency != account.currency.value:
            return UndecodableBody(
                f"{raw.transaction_id} in {raw.currency} on a {account.currency}"
                " account"
            )
        amount = _money(raw.amount, account.currency)
        if isinstance(amount, UndecodableBody):
            return amount
        balance_after = _money(raw.balance_after_mutation, account.currency)
        if isinstance(balance_after, UndecodableBody):
            return balance_after
        if amount.amount_minor == 0:
            return UndecodableBody(f"{raw.transaction_id} of nothing")
        found.append(
            BankTransaction(
                vendor_entry_id=BankTransactionId(raw.transaction_id),
                amount=amount,
                balance_after=balance_after,
                remittance=" ".join(raw.description_lines),
                counterparty=raw.counter_party_name,
                booked_on=raw.book_date,
            )
        )
    return tuple(found)
