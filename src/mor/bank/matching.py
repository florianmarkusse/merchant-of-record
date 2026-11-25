"""Which PSP payout each bank credit is (docs/plan-payments.md §7, the bank
statement):

    bank_entry_match (on a new entry, and on a payout reported): under the
        bank account's row lock, every unmatched credit against every
        unmatched payout paid out to it; a pair by `mor.bank.entries.pairs`
        is matched and booked
    a person, on the staff page: a credit no rule matched is linked to a
        payout of the same account, booked the same way; any other entry or
        a flagged payout gets a note, booking nothing

Both sides may come first: each one's fact runs the match again, and two
runs serialize on the bank account's lock. `bank_match` is unique on the
entry and on the payout, so nothing is matched twice.

Persistence functions take the connection first and never commit.
"""

from dataclasses import dataclass
from datetime import date, datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.bank.entries import (
    UnmatchedCredit,
    UnmatchedPayout,
    match_entry,
    pairs,
)
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.ids import (
    BankAccountId,
    BankEntryId,
    BankTransactionId,
    PspAccountId,
    PspPayoutId,
    SettlementPayoutId,
)
from mor.ledger.store import record_entry
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.money.amount import Money
from mor.money.currency import Currency


MATCHER = Actor(ActorKind.SYSTEM, "bank_entry_match")


@dataclass(frozen=True)
class LockedBankAccount:
    id: BankAccountId
    currency: Currency


async def lock_bank_account(
    conn: Connection, account_id: BankAccountId
) -> LockedBankAccount:
    async with conn.cursor(row_factory=class_row(LockedBankAccount)) as cur:
        await cur.execute(
            "SELECT id, currency FROM bank_account WHERE id = %s FOR UPDATE",
            (account_id,),
        )
        return await one_row(cur, f"bank account {account_id}")


async def destination_of(
    conn: Connection, payout_id: SettlementPayoutId
) -> BankAccountId | None:
    """The bank account a payout was paid out to, if its PSP account names
    one for its currency."""
    cur = await conn.execute(
        """
        SELECT d.bank_account_id
        FROM settlement_payout p
        JOIN psp_payout_destination d
          ON d.psp_account_id = p.account_id AND d.currency = p.currency
        WHERE p.id = %s
        """,
        (payout_id,),
    )
    row = await cur.fetchone()
    return None if row is None else BankAccountId(row[0])


async def _unmatched_credits(
    conn: Connection, account_id: BankAccountId
) -> list[UnmatchedCredit]:
    async with conn.cursor(row_factory=class_row(UnmatchedCredit)) as cur:
        await cur.execute(
            """
            SELECT e.id AS entry_id, e.remittance
            FROM bank_entry e
            WHERE e.bank_account_id = %s AND e.amount_minor > 0
              AND NOT EXISTS (SELECT 1 FROM bank_match m WHERE m.entry_id = e.id)
            ORDER BY e.id
            """,
            (account_id,),
        )
        return await cur.fetchall()


async def _unmatched_payouts(
    conn: Connection, account_id: BankAccountId
) -> list[UnmatchedPayout]:
    async with conn.cursor(row_factory=class_row(UnmatchedPayout)) as cur:
        await cur.execute(
            """
            SELECT p.id AS payout_id, p.vendor_payout_id
            FROM settlement_payout p
            JOIN psp_payout_destination d
              ON d.psp_account_id = p.account_id AND d.currency = p.currency
            WHERE d.bank_account_id = %s
              AND NOT EXISTS (SELECT 1 FROM bank_match m WHERE m.payout_id = p.id)
            ORDER BY p.id
            """,
            (account_id,),
        )
        return await cur.fetchall()


@dataclass(frozen=True, kw_only=True)
class _Credit:
    vendor_entry_id: BankTransactionId
    amount_minor: int
    currency: Currency
    booked_on: date


@dataclass(frozen=True)
class _Payout:
    account_id: PspAccountId
    vendor_payout_id: PspPayoutId


async def _book_match(
    conn: Connection,
    account: LockedBankAccount,
    entry_id: BankEntryId,
    payout_id: SettlementPayoutId,
    actor: Actor,
    now: datetime,
) -> None:
    """Record the pair and move what arrived out of suspense. Both sides
    were read unmatched under the account's lock."""
    async with conn.cursor(row_factory=class_row(_Credit)) as cur:
        await cur.execute(
            """
            SELECT vendor_entry_id, amount_minor, currency, booked_on
            FROM bank_entry WHERE id = %s
            """,
            (entry_id,),
        )
        found_credit = await one_row(cur, f"bank entry {entry_id}")
    async with conn.cursor(row_factory=class_row(_Payout)) as cur:
        await cur.execute(
            "SELECT account_id, vendor_payout_id FROM settlement_payout WHERE id = %s",
            (payout_id,),
        )
        payout = await one_row(cur, f"settlement payout {payout_id}")
    await conn.execute(
        """
        INSERT INTO bank_match (entry_id, payout_id, matched_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (entry_id, payout_id, now, actor.kind, actor.id),
    )
    await record_entry(
        conn,
        match_entry(
            account.id,
            found_credit.vendor_entry_id,
            payout.account_id,
            payout.vendor_payout_id,
            Money(found_credit.amount_minor, found_credit.currency),
            found_credit.booked_on,
            actor,
            now,
        ),
    )


async def match_account(
    conn: Connection, account: LockedBankAccount | None, now: datetime
) -> ConsumerOutcome:
    """Consumer `bank_entry_match`, under the bank account's lock: every
    pair the rule finds among what is unmatched. None: a payout whose PSP
    account names no destination for its currency, so it stays in
    transit."""
    if account is None:
        return Stale()
    unmatched_credits = await _unmatched_credits(conn, account.id)
    payouts = await _unmatched_payouts(conn, account.id)
    found = pairs(unmatched_credits, payouts)
    if not found:
        return Stale()
    for pair in found:
        await _book_match(conn, account, pair.entry_id, pair.payout_id, MATCHER, now)
    return Applied()


# --- a person, on the staff page ---------------------------------------------


@dataclass(frozen=True)
class Linked:
    pass


@dataclass(frozen=True)
class NotLinkable:
    """The entry is not an unmatched credit of this account, or the payout
    is not an unmatched payout paid out to it."""

    reason: str


async def link(
    conn: Connection,
    account: LockedBankAccount,
    entry_id: BankEntryId,
    payout_id: SettlementPayoutId,
    actor: Actor,
    now: datetime,
) -> Linked | NotLinkable:
    """A person says this credit is that payout: booked as a rule's match
    would be, with the person as actor."""
    unmatched_credits = await _unmatched_credits(conn, account.id)
    payouts = await _unmatched_payouts(conn, account.id)
    credit_found = False
    for unmatched in unmatched_credits:
        if unmatched.entry_id == entry_id:
            credit_found = True
    if not credit_found:
        return NotLinkable("not an unmatched credit of this account")
    payout_found = False
    for unmatched_payout in payouts:
        if unmatched_payout.payout_id == payout_id:
            payout_found = True
    if not payout_found:
        return NotLinkable("not an unmatched payout paid out to this account")
    await _book_match(conn, account, entry_id, payout_id, actor, now)
    return Linked()


@dataclass(frozen=True)
class Noted:
    pass


@dataclass(frozen=True)
class AlreadyNoted:
    pass


@dataclass(frozen=True)
class EmptyNote:
    pass


@dataclass(frozen=True)
class NothingToNote:
    pass


type NoteResult = Noted | AlreadyNoted | EmptyNote | NothingToNote


async def note_entry(
    conn: Connection,
    account_id: BankAccountId,
    entry_id: BankEntryId,
    note: str,
    actor: Actor,
    now: datetime,
) -> NoteResult:
    """A person says what an entry no payout explains is. Once: the insert
    is the guard. Books nothing."""
    if not note.strip():
        return EmptyNote()
    cur = await conn.execute(
        "SELECT 1 FROM bank_entry WHERE id = %s AND bank_account_id = %s",
        (entry_id, account_id),
    )
    if await cur.fetchone() is None:
        return NothingToNote()
    cur = await conn.execute(
        """
        INSERT INTO bank_entry_note (entry_id, note, noted_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (entry_id) DO NOTHING
        RETURNING entry_id
        """,
        (entry_id, note.strip(), now, actor.kind, actor.id),
    )
    if await cur.fetchone() is None:
        return AlreadyNoted()
    return Noted()


async def note_payout(
    conn: Connection,
    psp_account_id: PspAccountId,
    payout_id: SettlementPayoutId,
    note: str,
    actor: Actor,
    now: datetime,
) -> NoteResult:
    """A person says what they found about a flagged payout. Once. Books
    nothing."""
    if not note.strip():
        return EmptyNote()
    cur = await conn.execute(
        "SELECT 1 FROM settlement_payout WHERE id = %s AND account_id = %s",
        (payout_id, psp_account_id),
    )
    if await cur.fetchone() is None:
        return NothingToNote()
    cur = await conn.execute(
        """
        INSERT INTO settlement_payout_note
          (payout_id, note, noted_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (payout_id) DO NOTHING
        RETURNING payout_id
        """,
        (payout_id, note.strip(), now, actor.kind, actor.id),
    )
    if await cur.fetchone() is None:
        return AlreadyNoted()
    return Noted()
