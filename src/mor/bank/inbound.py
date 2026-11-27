"""What the bank shows us, stored raw and then decoded (docs/plan-payments.md
§7, the bank statement):

    a page of transactions ─▶ store_inbox (unique per account and body
        hash) + bank_inbox_stored
    bank_inbox_decode (per account): each entry inserted once by the bank's
        id and booked in the same transaction (`bank` against
        `bank_suspense`), with bank_entry_received for each new one

The guard against an entry booked twice is the bank's transaction id, never
the body hash, which only means "these bytes are stored already".
"""

import uuid
from datetime import datetime

from mor.audit import Actor, ActorKind
from mor.bank import registry
from mor.bank.accounts import read_bank_account
from mor.bank.entries import bank_entry
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.ids import BankAccountId, BankEntryId, BankInboxId
from mor.ledger.store import record_entry
from mor.messaging.facts import BankEntryReceivedV1, BankInboxStoredV1
from mor.messaging.outcome import Applied, ConsumerOutcome
from mor.messaging.store import append_fact
from mor.vendor_failures import UndecodableBody
from mor.vendor_inbox import AlreadyStored, Stored, UndecodableInbox, body_sha256


RECEIVER = Actor(ActorKind.SYSTEM, "bank_inbox")
DECODER = Actor(ActorKind.SYSTEM, "bank_inbox_decode")


async def store_inbox(
    conn: Connection, account_id: BankAccountId, body: bytes, now: datetime
) -> Stored[BankInboxId] | AlreadyStored:
    """Keep the raw bytes, nothing decoded, with the fact that starts the
    decoding. Insert, and a conflict means stored already. Never commits."""
    inbox_id = BankInboxId(uuid.uuid7())
    cur = await conn.execute(
        """
        INSERT INTO bank_inbox (id, bank_account_id, body_sha256, body, received_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (bank_account_id, body_sha256) DO NOTHING
        RETURNING id
        """,
        (inbox_id, account_id, body_sha256(body), body, now),
    )
    if await cur.fetchone() is None:
        return AlreadyStored()
    await append_fact(
        conn,
        BankInboxStoredV1(bank_account_id=account_id, inbox_id=inbox_id),
        RECEIVER,
        now,
    )
    return Stored(inbox_id)


async def decode_inbox(
    conn: Connection, stored_fact: BankInboxStoredV1, now: datetime
) -> ConsumerOutcome:
    """Consumer `bank_inbox_decode`, per account: each entry on the page
    once, booked as it is stored. Locks nothing: an inbox row never changes,
    and the entry's insert is its own guard."""
    account_id = stored_fact.bank_account_id
    inbox_id = stored_fact.inbox_id
    body = await one_value(
        conn, "SELECT body FROM bank_inbox WHERE id = %s", (inbox_id,), bytes
    )
    account = await read_bank_account(conn, account_id)
    assert account is not None, f"no bank account {account_id}"
    transactions = registry.decode_transactions(account, body)
    if isinstance(transactions, UndecodableBody):
        raise UndecodableInbox(f"bank inbox {inbox_id}: {transactions.detail}")
    if transactions:
        # The bank's balance after this page's last transaction, if this is
        # the newest page decoded yet.
        await conn.execute(
            """
            INSERT INTO bank_balance_shown (bank_account_id, inbox_id, balance_minor)
            VALUES (%s, %s, %s)
            ON CONFLICT (bank_account_id) DO UPDATE
            SET inbox_id = excluded.inbox_id,
                balance_minor = excluded.balance_minor
            WHERE bank_balance_shown.inbox_id < excluded.inbox_id
            """,
            (account_id, inbox_id, transactions[-1].balance_after.amount_minor),
        )
    for transaction in transactions:
        entry_id = BankEntryId(uuid.uuid7())
        cur = await conn.execute(
            """
            INSERT INTO bank_entry
              (id, bank_account_id, vendor_entry_id, amount_minor, currency,
               remittance, counterparty, booked_on, balance_after_minor,
               inbox_id, recorded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (bank_account_id, vendor_entry_id) DO NOTHING
            RETURNING id
            """,
            (
                entry_id,
                account_id,
                transaction.vendor_entry_id,
                transaction.amount.amount_minor,
                transaction.amount.currency,
                transaction.remittance,
                transaction.counterparty,
                transaction.booked_on,
                transaction.balance_after.amount_minor,
                inbox_id,
                now,
            ),
        )
        if await cur.fetchone() is None:
            continue  # listed before: stored and booked then
        await record_entry(
            conn,
            bank_entry(
                account_id,
                transaction.vendor_entry_id,
                transaction.amount,
                transaction.booked_on,
                DECODER,
                now,
            ),
        )
        await append_fact(
            conn,
            BankEntryReceivedV1(bank_account_id=account_id, bank_entry_id=entry_id),
            DECODER,
            now,
        )
    return Applied()
