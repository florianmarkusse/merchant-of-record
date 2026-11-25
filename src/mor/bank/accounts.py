"""Our bank accounts and where each PSP account pays out
(docs/plan-payments.md §7, the bank statement). Feature code never names a
bank: which account a payout lands in is data."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from psycopg.rows import class_row

from mor.bank.port import BankAccount, BankProvider
from mor.db.connect import Connection
from mor.ids import BankAccountId, PspAccountId
from mor.money.currency import Currency
from mor.provider_accounts import ProviderAccountKind


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: BankAccountId
    name: str
    provider: BankProvider
    kind: ProviderAccountKind
    base_url: str
    timeout_ms: int
    external_ref: str
    currency: Currency
    statement_interval_s: int


def _account(row: _AccountRow) -> BankAccount:
    return BankAccount(
        id=row.id,
        name=row.name,
        provider=row.provider,
        kind=row.kind,
        base_url=row.base_url,
        timeout=timedelta(milliseconds=row.timeout_ms),
        external_ref=row.external_ref,
        currency=row.currency,
        statement_interval=timedelta(seconds=row.statement_interval_s),
    )


async def create_bank_account(
    conn: Connection,
    name: str,
    provider: BankProvider,
    kind: ProviderAccountKind,
    base_url: str,
    external_ref: str,
    currency: Currency,
    now: datetime,
    timeout: timedelta = timedelta(seconds=10),
    statement_interval: timedelta = timedelta(hours=1),
) -> BankAccount:
    """Its statement is read once `mor.bank.fetch.start_reading` starts it,
    as the seed does. Never commits."""
    account_id = BankAccountId(uuid.uuid7())
    await conn.execute(
        """
        INSERT INTO bank_account
          (id, name, provider, kind, base_url, timeout_ms, external_ref,
           currency, statement_interval_s, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            account_id,
            name,
            provider,
            kind,
            base_url,
            int(timeout.total_seconds() * 1000),
            external_ref,
            currency,
            int(statement_interval.total_seconds()),
            now,
        ),
    )
    return BankAccount(
        id=account_id,
        name=name,
        provider=provider,
        kind=kind,
        base_url=base_url,
        timeout=timeout,
        external_ref=external_ref,
        currency=currency,
        statement_interval=statement_interval,
    )


_SELECT = """
    SELECT id, name, provider, kind, base_url, timeout_ms, external_ref,
           currency, statement_interval_s
    FROM bank_account
"""


async def read_bank_account(
    conn: Connection, account_id: BankAccountId
) -> BankAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(_SELECT + " WHERE id = %s", (account_id,))
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def bank_account_by_name(conn: Connection, name: str) -> BankAccount | None:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(_SELECT + " WHERE name = %s", (name,))
        row = await cur.fetchone()
    return None if row is None else _account(row)


async def set_payout_destination(
    conn: Connection,
    psp_account_id: PspAccountId,
    bank_account: BankAccount,
    now: datetime,
) -> None:
    """Where `psp_account_id` pays out the bank account's currency. Set once:
    a second call for the same account and currency changes nothing, so a
    payout's destination never moves. Never commits."""
    await conn.execute(
        """
        INSERT INTO psp_payout_destination
          (psp_account_id, currency, bank_account_id, created_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (psp_account_id, currency) DO NOTHING
        """,
        (psp_account_id, bank_account.currency, bank_account.id, now),
    )
