"""The bank port: our records and result unions, no provider words
(docs/plan-payments.md §7, the bank statement).

A bank account is one currency balance at one provider (an IBAN, a Wise
balance, an Airwallex global account). A provider module reads its
transactions, converts amounts to our minor units and never touches our
database. Reading is all we do, so no call needs a duplicate guard.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from mor.ids import BankAccountId, BankTransactionId
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind
from mor.vendor_failures import CallFailure


class BankProvider(StrEnum):
    """The module that speaks to a bank; matches `bank_provider`."""

    FAKE = "fake"


@dataclass(frozen=True, kw_only=True)
class BankAccount:
    id: BankAccountId
    name: str
    provider: BankProvider
    kind: ProviderAccountKind
    base_url: str
    timeout: timedelta
    external_ref: str  # what the bank calls it: an IBAN, a balance id
    currency: Currency
    statement_interval: timedelta


@dataclass(frozen=True, kw_only=True)
class BankTransaction:
    """One entry the bank shows. `amount` is signed: a credit is positive.
    `balance_after` is the bank's balance once it is booked."""

    vendor_entry_id: BankTransactionId
    amount: Money
    balance_after: Money
    remittance: str
    counterparty: str
    booked_on: date


@dataclass(frozen=True)
class TransactionsPage:
    """A page of transactions, raw, with what the reader needs to go on: the
    bank's key for the position after it (None for an empty page) and
    whether more follow. A position, never a transaction id: a bank that
    lists a transaction twice would make "after this id" ambiguous."""

    body: bytes
    next_page_key: str | None
    has_more: bool


type PageResult = TransactionsPage | CallFailure
