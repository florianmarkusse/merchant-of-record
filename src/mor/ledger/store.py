import uuid
from dataclasses import dataclass

from psycopg.rows import class_row

from mor.db.connect import Connection
from mor.db.fetch import one_row, one_value
from mor.ledger.account import Account, kind_of, scope_of
from mor.ledger.entry import JournalEntry
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
class _Comparison:
    id: uuid.UUID
    same: bool


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
                "kind": kind_of(line.account).value,
                "scope": scope_of(line.account),
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
    """The entry recorded under this key must be this one: the same
    description, moments, actor and lines (line numbers included). The
    lines are compared in the database, both ways with `EXCEPT`."""
    line_nos: list[int] = []
    kinds: list[str] = []
    scopes: list[str] = []
    amounts: list[int] = []
    currencies: list[str] = []
    for line_no, line in enumerate(entry.lines, start=1):
        line_nos.append(line_no)
        kinds.append(kind_of(line.account).value)
        scopes.append(scope_of(line.account))
        amounts.append(line.amount.amount_minor)
        currencies.append(line.amount.currency.value)
    async with conn.cursor(row_factory=class_row(_Comparison)) as cur:
        await cur.execute(
            """
            WITH given AS (
              SELECT * FROM unnest(
                %(line_nos)s::int[], %(kinds)s::account_kind[],
                %(scopes)s::text[], %(amounts)s::bigint[],
                %(currencies)s::currency[]
              ) AS g (line_no, account_kind, account_scope, amount_minor,
                      currency)
            ), stored AS (
              SELECT l.line_no, l.account_kind, l.account_scope,
                     l.amount_minor, l.currency
              FROM journal_line l JOIN journal_entry e ON e.id = l.entry_id
              WHERE e.idempotency_key = %(key)s
            )
            SELECT e.id,
                   e.description = %(description)s
                   AND e.effective_at = %(effective_at)s
                   AND e.actor_kind = %(actor_kind)s::actor_kind
                   AND e.actor_id = %(actor_id)s
                   AND NOT EXISTS (SELECT * FROM given EXCEPT SELECT * FROM stored)
                   AND NOT EXISTS (SELECT * FROM stored EXCEPT SELECT * FROM given)
                     AS same
            FROM journal_entry e WHERE e.idempotency_key = %(key)s
            """,
            {
                "key": entry.idempotency_key,
                "description": entry.description,
                "effective_at": entry.effective_at,
                "actor_kind": entry.actor.kind.value,
                "actor_id": entry.actor.id,
                "line_nos": line_nos,
                "kinds": kinds,
                "scopes": scopes,
                "amounts": amounts,
                "currencies": currencies,
            },
        )
        recorded = await one_row(cur, f"journal entry {entry.idempotency_key}")
    if not recorded.same:
        raise IdempotencyKeyReused(entry.idempotency_key)
    return AlreadyRecorded(recorded.id)


async def balance(conn: Connection, account: Account, currency: Currency) -> Money:
    """An account's balance in one currency, summed from its lines (debit
    positive). Balances are never stored."""
    total = await one_value(
        conn,
        """
        SELECT COALESCE(sum(amount_minor), 0)::bigint
        FROM journal_line
        WHERE account_kind = %(kind)s AND account_scope = %(scope)s
          AND currency = %(currency)s
        """,
        {
            "kind": kind_of(account).value,
            "scope": scope_of(account),
            "currency": currency.value,
        },
        int,
    )
    return Money(total, currency)
