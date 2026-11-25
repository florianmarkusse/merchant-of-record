"""What a person reviews of our bank accounts (docs/plan-payments.md §7,
the bank statement): per account its `bank` balance beside the bank's own,
what sits in suspense, and its entries, unmatched ones first with the
payouts a person may link them to.

Persistence functions take the connection first and never commit.
"""

from dataclasses import dataclass
from datetime import date, datetime

from psycopg.rows import class_row

from mor.db.connect import Connection
from mor.ids import (
    BankAccountId,
    BankEntryId,
    BankTransactionId,
    PspPayoutId,
    SettlementPayoutId,
)
from mor.money.currency import Currency


@dataclass(frozen=True, kw_only=True)
class BankSummary:
    """`bank_balance_minor`: the bank's balance after the last transaction
    of the newest page decoded, None before the first. Our `bank` must
    equal it once every page read is decoded."""

    id: BankAccountId
    name: str
    currency: Currency
    external_ref: str
    ledger_minor: int
    bank_balance_minor: int | None
    suspense_minor: int
    unexplained: int


async def bank_summaries(conn: Connection) -> list[BankSummary]:
    async with conn.cursor(row_factory=class_row(BankSummary)) as cur:
        await cur.execute(
            """
            SELECT a.id, a.name, a.currency, a.external_ref,
              (SELECT coalesce(sum(l.amount_minor), 0)::bigint FROM journal_line l
               WHERE l.account_kind = 'bank' AND l.account_scope = a.id::text
                 AND l.currency = a.currency) AS ledger_minor,
              (SELECT s.balance_minor FROM bank_balance_shown s
               WHERE s.bank_account_id = a.id) AS bank_balance_minor,
              (SELECT coalesce(sum(l.amount_minor), 0)::bigint FROM journal_line l
               WHERE l.account_kind = 'bank_suspense'
                 AND l.account_scope = a.id::text
                 AND l.currency = a.currency) AS suspense_minor,
              (SELECT count(*) FROM bank_entry e
               WHERE e.bank_account_id = a.id
                 AND NOT EXISTS (SELECT 1 FROM bank_match m WHERE m.entry_id = e.id)
                 AND NOT EXISTS (SELECT 1 FROM bank_entry_note n
                                 WHERE n.entry_id = e.id)) AS unexplained
            FROM bank_account a
            ORDER BY a.name
            """
        )
        return await cur.fetchall()


async def bank_summary(
    conn: Connection, account_id: BankAccountId
) -> BankSummary | None:
    for summary in await bank_summaries(conn):
        if summary.id == account_id:
            return summary
    return None


@dataclass(frozen=True, kw_only=True)
class EntryRow:
    """An entry and what explains it: a payout (by rule or by a person), a
    note, or nothing yet."""

    id: BankEntryId
    vendor_entry_id: BankTransactionId
    amount_minor: int
    currency: Currency
    remittance: str
    counterparty: str
    booked_on: date
    payout: PspPayoutId | None
    matched_by: str | None
    note: str | None
    noted_by: str | None
    noted_at: datetime | None


async def entries_of(
    conn: Connection, account_id: BankAccountId, limit: int
) -> list[EntryRow]:
    """Unexplained ones first, then the newest."""
    async with conn.cursor(row_factory=class_row(EntryRow)) as cur:
        await cur.execute(
            """
            SELECT e.id, e.vendor_entry_id, e.amount_minor, e.currency,
                   e.remittance, e.counterparty, e.booked_on,
                   p.vendor_payout_id AS payout, m.actor_id AS matched_by,
                   n.note, n.actor_id AS noted_by, n.noted_at
            FROM bank_entry e
            LEFT JOIN bank_match m ON m.entry_id = e.id
            LEFT JOIN settlement_payout p ON p.id = m.payout_id
            LEFT JOIN bank_entry_note n ON n.entry_id = e.id
            WHERE e.bank_account_id = %s
            ORDER BY (m.entry_id IS NULL AND n.entry_id IS NULL) DESC,
                     e.booked_on DESC, e.id DESC
            LIMIT %s
            """,
            (account_id, limit),
        )
        return await cur.fetchall()


@dataclass(frozen=True, kw_only=True)
class LinkablePayout:
    """A payout paid out to this account and not matched yet: what a person
    may link a credit to."""

    id: SettlementPayoutId
    vendor_payout_id: PspPayoutId
    psp_account: str
    net_minor: int
    paid_out_at: datetime


async def linkable_payouts(
    conn: Connection, account_id: BankAccountId
) -> list[LinkablePayout]:
    async with conn.cursor(row_factory=class_row(LinkablePayout)) as cur:
        await cur.execute(
            """
            SELECT p.id, p.vendor_payout_id, a.name AS psp_account, p.net_minor,
                   p.paid_out_at
            FROM settlement_payout p
            JOIN psp_account a ON a.id = p.account_id
            JOIN psp_payout_destination d
              ON d.psp_account_id = p.account_id AND d.currency = p.currency
            WHERE d.bank_account_id = %s
              AND NOT EXISTS (SELECT 1 FROM bank_match m WHERE m.payout_id = p.id)
            ORDER BY p.paid_out_at, p.id
            """,
            (account_id,),
        )
        return await cur.fetchall()
