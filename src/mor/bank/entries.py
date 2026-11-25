"""The bank's journal entries and the matching rule (docs/plan-payments.md
§7, the bank statement). Pure.

    an entry the bank shows, as it is stored, in its currency:
      Dr bank:{bank account}             the amount (a debit the other way)
      Cr bank_suspense:{bank account}    the same
    a credit matched to a PSP payout, by rule or by a person:
      Dr bank_suspense:{bank account}    what arrived
      Cr payout_in_transit:{psp account} the same

What arrived is matched whatever the payout said: a shortfall or an excess
stays in `payout_in_transit`, shown on the payout, never written off here.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from mor.audit import Actor
from mor.ids import (
    BankAccountId,
    BankEntryId,
    BankTransactionId,
    JournalKey,
    PspAccountId,
    PspPayoutId,
    SettlementPayoutId,
)
from mor.ledger.account import Account, Bank, BankSuspense, PayoutInTransit
from mor.ledger.entry import JournalEntry, Line, build_entry, credit, debit
from mor.money.amount import Money


def entry_key(
    bank_account_id: BankAccountId, vendor_entry_id: BankTransactionId
) -> JournalKey:
    return JournalKey(f"bank-entry:{bank_account_id}:{vendor_entry_id}")


def match_key(
    bank_account_id: BankAccountId, vendor_entry_id: BankTransactionId
) -> JournalKey:
    return JournalKey(f"bank-match:{bank_account_id}:{vendor_entry_id}")


def booked_at(booked_on: date) -> datetime:
    """A bank books by day: the entry is effective at its start, in UTC."""
    return datetime.combine(booked_on, time(), UTC)


def _post(lines: list[Line], account: Account, amount: Money) -> None:
    """Debit a positive amount, credit a negative one."""
    if amount.amount_minor > 0:
        lines.append(debit(account, amount))
    else:
        lines.append(credit(account, Money(-amount.amount_minor, amount.currency)))


def bank_entry(
    bank_account_id: BankAccountId,
    vendor_entry_id: BankTransactionId,
    amount: Money,
    booked_on: date,
    actor: Actor,
    now: datetime,
) -> JournalEntry:
    """What the bank shows, in `bank`, and unexplained until matched.
    `amount` is signed and never zero."""
    lines: list[Line] = []
    _post(lines, Bank(bank_account_id), amount)
    _post(
        lines,
        BankSuspense(bank_account_id),
        Money(-amount.amount_minor, amount.currency),
    )
    return build_entry(
        entry_key(bank_account_id, vendor_entry_id),
        f"bank entry {vendor_entry_id}",
        booked_at(booked_on),
        actor,
        lines,
        now,
    )


def match_entry(
    bank_account_id: BankAccountId,
    vendor_entry_id: BankTransactionId,
    psp_account_id: PspAccountId,
    vendor_payout_id: PspPayoutId,
    arrived: Money,
    booked_on: date,
    actor: Actor,
    now: datetime,
) -> JournalEntry:
    """A credit explained as a PSP's payout: what arrived leaves suspense
    and clears what the payout put in transit."""
    lines: list[Line] = []
    _post(lines, BankSuspense(bank_account_id), arrived)
    _post(
        lines,
        PayoutInTransit(psp_account_id),
        Money(-arrived.amount_minor, arrived.currency),
    )
    return build_entry(
        match_key(bank_account_id, vendor_entry_id),
        f"bank entry {vendor_entry_id} is payout {vendor_payout_id}",
        booked_at(booked_on),
        actor,
        lines,
        now,
    )


# --- matching -----------------------------------------------------------------


@dataclass(frozen=True)
class UnmatchedCredit:
    entry_id: BankEntryId
    remittance: str


@dataclass(frozen=True)
class UnmatchedPayout:
    payout_id: SettlementPayoutId
    vendor_payout_id: PspPayoutId


@dataclass(frozen=True)
class Pair:
    entry_id: BankEntryId
    payout_id: SettlementPayoutId


_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def tokens(remittance: str) -> frozenset[str]:
    """The words of a remittance text a reference can be: a payout's id is
    one of them whole, never a part of one (`stl_1` is not in `stl_12`)."""
    return frozenset(_TOKEN.findall(remittance))


def pairs(
    unmatched_credits: Sequence[UnmatchedCredit], payouts: Sequence[UnmatchedPayout]
) -> list[Pair]:
    """A credit and a payout pair when the payout's id is a whole word of
    the credit's text and each is the other's only candidate. Never by
    amount: two candidates on either side pair nothing, for a person."""
    candidates: dict[BankEntryId, list[SettlementPayoutId]] = {}
    claimed_by: dict[SettlementPayoutId, int] = {}
    for unmatched in unmatched_credits:
        words = tokens(unmatched.remittance)
        found: list[SettlementPayoutId] = []
        for payout in payouts:
            if payout.vendor_payout_id in words:
                found.append(payout.payout_id)
                claimed_by[payout.payout_id] = claimed_by.get(payout.payout_id, 0) + 1
        candidates[unmatched.entry_id] = found
    paired: list[Pair] = []
    for unmatched in unmatched_credits:
        found = candidates[unmatched.entry_id]
        if len(found) == 1 and claimed_by[found[0]] == 1:
            paired.append(Pair(unmatched.entry_id, found[0]))
    return paired
