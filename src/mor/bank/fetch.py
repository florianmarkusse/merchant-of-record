"""Reading a bank account's transactions (docs/plan-payments.md §7, the
bank statement):

    start_reading ─▶ bank_statement_due (scheduled, per bank account)
    bank_statement_fetch (effect), one look: a page after the cursor, the
    bank's own page key
        transactions on it ─▶ stored raw in the inbox, the cursor moves to
            the key after it; more promised: the next look now, in a
            delivery of its own; none: after the account's interval
        an empty page ─▶ the next look after the account's interval

A read needs no guard: the inbox absorbs bytes stored twice, and every
entry is stored once by the bank's own id. Decoding is the inbox's.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx2
from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.bank import registry
from mor.bank.accounts import read_bank_account
from mor.bank.inbound import store_inbox
from mor.bank.port import BankAccount, PageResult, TransactionsPage
from mor.config import Environment
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.ids import BankAccountId
from mor.messaging.facts import BankStatementDueV1
from mor.messaging.outcome import Applied, Done, RetryLater
from mor.messaging.store import append_fact
from mor.vendor_failures import OutcomeUnknown, Unavailable, UndecodableBody


SCHEDULER = Actor(ActorKind.SYSTEM, "bank_statement_fetch")


@dataclass(frozen=True)
class PageCall:
    account: BankAccount
    page_key: str | None


@dataclass(frozen=True)
class _Cursor:
    page_key: str | None


async def start_reading(
    conn: Connection, account_id: BankAccountId, actor: Actor, now: datetime
) -> bool:
    """An account's first look, now; each look schedules the next. The
    cursor's insert is the guard: an account already reading is left as it
    is. Never commits."""
    cur = await conn.execute(
        """
        INSERT INTO bank_cursor (bank_account_id, started_at) VALUES (%s, %s)
        ON CONFLICT (bank_account_id) DO NOTHING RETURNING bank_account_id
        """,
        (account_id, now),
    )
    if await cur.fetchone() is None:
        return False
    await append_fact(conn, BankStatementDueV1(bank_account_id=account_id), actor, now)
    return True


async def _lock_cursor(conn: Connection, account_id: BankAccountId) -> _Cursor:
    async with conn.cursor(row_factory=class_row(_Cursor)) as cur:
        await cur.execute(
            "SELECT page_key FROM bank_cursor WHERE bank_account_id = %s FOR UPDATE",
            (account_id,),
        )
        return await one_row(cur, f"bank cursor for {account_id}")


# --- the consumer bank_statement_fetch: prepare_fetch, read_page,
# complete_fetch, run by mor.messaging.delivery.run_steps ----------------------


async def prepare_fetch(
    conn: Connection, account_id: BankAccountId, now: datetime
) -> PageCall:
    account = await read_bank_account(conn, account_id)
    assert account is not None, f"no bank account {account_id}"
    cursor = await _lock_cursor(conn, account_id)
    return PageCall(account, cursor.page_key)


async def read_page(
    http: httpx2.AsyncClient, environment: Environment, page_call: PageCall
) -> PageResult:
    return await registry.list_transactions(
        http, environment, page_call.account, page_call.page_key
    )


async def complete_fetch(
    conn: Connection,
    account_id: BankAccountId,
    page_call: PageCall,
    result: PageResult,
    now: datetime,
) -> Done | RetryLater:
    await _lock_cursor(conn, account_id)
    match result:
        case TransactionsPage(
            body=body, next_page_key=next_page_key, has_more=has_more
        ):
            if next_page_key is None:
                if has_more:
                    return RetryLater("bank: more promised after an empty page")
                await _look_later(conn, page_call.account, now)
                return Done(Applied())
            await store_inbox(conn, account_id, body, now)
            await conn.execute(
                "UPDATE bank_cursor SET page_key = %s WHERE bank_account_id = %s",
                (next_page_key, account_id),
            )
            if has_more:
                await append_fact(
                    conn, BankStatementDueV1(bank_account_id=account_id), SCHEDULER, now
                )
            else:
                await _look_later(conn, page_call.account, now)
            return Done(Applied())
        case Unavailable(detail=detail) | OutcomeUnknown(detail=detail):
            # A read: trying again later is always safe.
            return RetryLater(f"bank: {detail}")
        case UndecodableBody(detail=detail):
            # An answer the bank does not document: retried, and visible as
            # a failing delivery until a person or a fixed client reads it.
            return RetryLater(f"bank answer: {detail}")


async def _look_later(conn: Connection, account: BankAccount, now: datetime) -> None:
    await append_fact(
        conn,
        BankStatementDueV1(bank_account_id=account.id),
        SCHEDULER,
        now,
        deliver_at=now + account.statement_interval,
    )
