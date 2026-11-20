"""What a person reviews of settlement (docs/plan-payments.md §7.1): per
PSP account its health figures, its batches and its exceptions, and the
note a person writes on resolving one. A note books nothing (question 10
of 20-Nov-2026): a correcting entry is made by hand.

Persistence functions take the connection first and never commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection


@dataclass(frozen=True)
class Balance:
    """In one currency: what the PSP holds for us by our books, and what it
    says it paid out that our bank has not shown yet."""

    currency: str
    clearing_minor: int
    in_transit_minor: int


@dataclass(frozen=True)
class AccountSummary:
    id: uuid.UUID
    name: str
    settlement_mode: str
    last_read: str | None
    balances: tuple[Balance, ...]
    open_exceptions: int


@dataclass(frozen=True)
class _AccountRow:
    id: uuid.UUID
    name: str
    settlement_mode: str
    last_read: str | None
    open_exceptions: int


async def _balances(conn: Connection, account_id: uuid.UUID) -> tuple[Balance, ...]:
    cur = await conn.execute(
        """
        SELECT currency::text,
          coalesce(sum(amount_minor) FILTER (WHERE account_kind = 'psp_clearing'),
                   0)::bigint,
          coalesce(sum(amount_minor)
                     FILTER (WHERE account_kind = 'payout_in_transit'), 0)::bigint
        FROM journal_line
        WHERE account_kind IN ('psp_clearing', 'payout_in_transit')
          AND account_scope = %s
        GROUP BY currency ORDER BY currency
        """,
        (str(account_id),),
    )
    found: list[Balance] = []
    for currency, clearing, in_transit in await cur.fetchall():
        found.append(Balance(currency, clearing, in_transit))
    return tuple(found)


async def account_summaries(conn: Connection) -> list[AccountSummary]:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT a.id, a.name, a.settlement_mode::text AS settlement_mode,
                   c.last_read,
                   (SELECT count(*) FROM settlement_exception x
                    WHERE x.account_id = a.id AND NOT EXISTS (
                      SELECT 1 FROM settlement_exception_resolved r
                      WHERE r.exception_id = x.id)) AS open_exceptions
            FROM psp_account a
            LEFT JOIN psp_settlement_cursor c ON c.account_id = a.id
            ORDER BY a.name
            """
        )
        rows = await cur.fetchall()
    found: list[AccountSummary] = []
    for row in rows:
        found.append(
            AccountSummary(
                row.id,
                row.name,
                row.settlement_mode,
                row.last_read,
                await _balances(conn, row.id),
                row.open_exceptions,
            )
        )
    return found


async def account_summary(
    conn: Connection, account_id: uuid.UUID
) -> AccountSummary | None:
    for summary in await account_summaries(conn):
        if summary.id == account_id:
            return summary
    return None


@dataclass(frozen=True)
class BatchRow:
    vendor_batch_id: str
    currency: str
    net_minor: int
    paid_out_at: datetime
    booked_at: datetime | None
    lines: int
    booked_lines: int


async def batches_of(
    conn: Connection, account_id: uuid.UUID, limit: int
) -> list[BatchRow]:
    """The newest first."""
    async with conn.cursor(row_factory=class_row(BatchRow)) as cur:
        await cur.execute(
            """
            SELECT b.vendor_batch_id, b.currency::text AS currency, b.net_minor,
                   b.paid_out_at, b.booked_at,
                   (SELECT count(*) FROM settlement_line l
                    WHERE l.batch_id = b.id) AS lines,
                   (SELECT count(*) FROM settlement_line l
                    WHERE l.batch_id = b.id AND l.state = 'booked') AS booked_lines
            FROM settlement_batch b WHERE b.account_id = %s
            ORDER BY b.recorded_at DESC, b.id DESC LIMIT %s
            """,
            (account_id, limit),
        )
        return await cur.fetchall()


@dataclass(frozen=True)
class ExceptionRow:
    id: uuid.UUID
    kind: str
    detail: str
    created_at: datetime
    vendor_ref: str  # the PSP's line id, or its batch id
    note: str | None
    resolved_by: str | None
    resolved_at: datetime | None


async def exceptions_of(conn: Connection, account_id: uuid.UUID) -> list[ExceptionRow]:
    """Open ones first, then the newest."""
    async with conn.cursor(row_factory=class_row(ExceptionRow)) as cur:
        await cur.execute(
            """
            SELECT x.id, x.kind::text AS kind, x.detail, x.created_at,
                   coalesce(l.vendor_line_id, b.vendor_batch_id) AS vendor_ref,
                   r.note, r.actor_id AS resolved_by, r.resolved_at
            FROM settlement_exception x
            LEFT JOIN settlement_line l ON l.id = x.line_id
            LEFT JOIN settlement_batch b ON b.id = x.batch_id
            LEFT JOIN settlement_exception_resolved r ON r.exception_id = x.id
            WHERE x.account_id = %s
            ORDER BY (r.exception_id IS NULL) DESC, x.created_at DESC, x.id DESC
            """,
            (account_id,),
        )
        return await cur.fetchall()


@dataclass(frozen=True)
class Resolved:
    pass


@dataclass(frozen=True)
class AlreadyResolved:
    pass


@dataclass(frozen=True)
class NoSuchException:
    pass


@dataclass(frozen=True)
class EmptyNote:
    pass


type ResolveResult = Resolved | AlreadyResolved | NoSuchException | EmptyNote


async def resolve_exception(
    conn: Connection,
    account_id: uuid.UUID,
    exception_id: uuid.UUID,
    note: str,
    actor: Actor,
    now: datetime,
) -> ResolveResult:
    """Record that a person looked, once: the insert is the guard, so a
    second form sent leaves the first note as it is."""
    if not note.strip():
        return EmptyNote()
    cur = await conn.execute(
        "SELECT 1 FROM settlement_exception WHERE id = %s AND account_id = %s",
        (exception_id, account_id),
    )
    if await cur.fetchone() is None:
        return NoSuchException()
    cur = await conn.execute(
        """
        INSERT INTO settlement_exception_resolved
          (exception_id, note, resolved_at, actor_kind, actor_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (exception_id) DO NOTHING
        RETURNING exception_id
        """,
        (exception_id, note.strip(), now, actor.kind.value, actor.id),
    )
    if await cur.fetchone() is None:
        return AlreadyResolved()
    return Resolved()
