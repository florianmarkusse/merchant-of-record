import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.ledger.account import Account, AccountKind
from mor.ledger.entry import JournalEntry, Line
from mor.money.amount import Money
from mor.money.currency import Currency


class IdempotencyKeyReused(Exception):
    """One key was used for two different entries: a bug in the caller, like
    a merchant API's 409."""


@dataclass(frozen=True)
class Recorded:
    entry_id: uuid.UUID


@dataclass(frozen=True)
class AlreadyRecorded:
    """The same entry was recorded before under this key (a retry, a
    redelivered message); nothing was written."""

    entry_id: uuid.UUID


@dataclass(frozen=True)
class _EntryRow:
    id: uuid.UUID
    description: str
    effective_at: datetime
    actor_kind: str
    actor_id: str


@dataclass(frozen=True)
class _LineRow:
    account_kind: str
    account_scope: str
    amount_minor: int
    currency: str


async def record_entry(
    conn: Connection, entry: JournalEntry
) -> Recorded | AlreadyRecorded:
    """Append a balanced entry in the caller's transaction. The unique
    idempotency key decides "already recorded": insert first, and only on a
    conflict read what is there. Never commits."""
    cur = await conn.execute(
        """
        INSERT INTO journal_entry
          (id, idempotency_key, description, effective_at, recorded_at,
           actor_kind, actor_id)
        VALUES
          (%(id)s, %(key)s, %(description)s, %(effective_at)s, %(recorded_at)s,
           %(actor_kind)s, %(actor_id)s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        {
            "id": entry.id,
            "key": entry.idempotency_key,
            "description": entry.description,
            "effective_at": entry.effective_at,
            "recorded_at": entry.recorded_at,
            "actor_kind": entry.actor.kind.value,
            "actor_id": entry.actor.id,
        },
    )
    if await cur.fetchone() is None:
        return await _compare_with_recorded(conn, entry)

    params: list[dict[str, object]] = []
    for line_no, line in enumerate(entry.lines, start=1):
        params.append(
            {
                "entry_id": entry.id,
                "line_no": line_no,
                "kind": line.account.kind.value,
                "scope": line.account.scope,
                "amount_minor": line.amount.amount_minor,
                "currency": line.amount.currency.value,
            }
        )
    async with conn.cursor() as line_cur:
        await line_cur.executemany(
            """
            INSERT INTO journal_line
              (entry_id, line_no, account_kind, account_scope, amount_minor,
               currency)
            VALUES
              (%(entry_id)s, %(line_no)s, %(kind)s, %(scope)s, %(amount_minor)s,
               %(currency)s)
            """,
            params,
        )
    return Recorded(entry.id)


async def _compare_with_recorded(
    conn: Connection, entry: JournalEntry
) -> AlreadyRecorded:
    async with conn.cursor(row_factory=class_row(_EntryRow)) as cur:
        await cur.execute(
            """
            SELECT id, description, effective_at, actor_kind::text, actor_id
            FROM journal_entry WHERE idempotency_key = %(key)s
            """,
            {"key": entry.idempotency_key},
        )
        recorded = await cur.fetchone()
    assert recorded is not None, "conflict on a key that has no row"

    async with conn.cursor(row_factory=class_row(_LineRow)) as cur:
        await cur.execute(
            """
            SELECT account_kind::text, account_scope, amount_minor, currency::text
            FROM journal_line WHERE entry_id = %(entry_id)s ORDER BY line_no
            """,
            {"entry_id": recorded.id},
        )
        line_rows = await cur.fetchall()
    recorded_lines: list[Line] = []
    for row in line_rows:
        recorded_lines.append(
            Line(
                Account(AccountKind(row.account_kind), row.account_scope),
                Money(row.amount_minor, Currency(row.currency)),
            )
        )

    same = (
        recorded.description == entry.description
        and recorded.effective_at == entry.effective_at
        and Actor(ActorKind(recorded.actor_kind), recorded.actor_id) == entry.actor
        and tuple(recorded_lines) == entry.lines
    )
    if not same:
        raise IdempotencyKeyReused(entry.idempotency_key)
    return AlreadyRecorded(recorded.id)


async def balance(conn: Connection, account: Account, currency: Currency) -> Money:
    """An account's balance in one currency, summed from its lines (debit
    positive). Balances are never stored."""
    cur = await conn.execute(
        """
        SELECT COALESCE(sum(amount_minor), 0)::bigint
        FROM journal_line
        WHERE account_kind = %(kind)s AND account_scope = %(scope)s
          AND currency = %(currency)s
        """,
        {
            "kind": account.kind.value,
            "scope": account.scope,
            "currency": currency.value,
        },
    )
    row = await cur.fetchone()
    assert row is not None
    total = row[0]
    assert isinstance(total, int)
    return Money(total, currency)
