"""What a person reviews of settlement (docs/plan-payments.md §7,
docs/plan-payments.md §7): per PSP account its health
figures, its payouts with whether each adds up yet, its exceptions, and
the note a person writes on resolving one. A note books nothing (question 10
of 20-Nov-2026): a correcting entry is made by hand.

Persistence functions take the connection first and never commit.
"""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from psycopg.rows import class_row

from mor.audit import Actor
from mor.db.connect import Connection
from mor.ids import PspAccountId, PspPayoutId, SettlementExceptionId
from mor.money.currency import Currency
from mor.psp.port import SettlementMode
from mor.settlement.records import ExceptionKind, Reconciliation


@dataclass(frozen=True)
class Balance:
    """In one currency: what the PSP holds for us by our books, and what it
    says it paid out that our bank has not shown yet."""

    currency: Currency
    clearing_minor: int
    in_transit_minor: int


@dataclass(frozen=True, kw_only=True)
class AccountSummary:
    id: PspAccountId
    name: str
    settlement_mode: SettlementMode
    last_read: str | None
    balances: tuple[Balance, ...]
    open_exceptions: int
    open_payouts: int


@dataclass(frozen=True, kw_only=True)
class _AccountRow:
    id: PspAccountId
    name: str
    settlement_mode: SettlementMode
    last_read: str | None
    open_exceptions: int
    open_payouts: int


async def _balances(conn: Connection, account_id: PspAccountId) -> tuple[Balance, ...]:
    async with conn.cursor(row_factory=class_row(Balance)) as cur:
        await cur.execute(
            """
            SELECT currency,
              coalesce(sum(amount_minor)
                         FILTER (WHERE account_kind = 'psp_clearing'),
                       0)::bigint AS clearing_minor,
              coalesce(sum(amount_minor)
                         FILTER (WHERE account_kind = 'payout_in_transit'),
                       0)::bigint AS in_transit_minor
            FROM journal_line
            WHERE account_kind IN ('psp_clearing', 'payout_in_transit')
              AND account_scope = %s
            GROUP BY currency ORDER BY currency
            """,
            (str(account_id),),
        )
        return tuple(await cur.fetchall())


async def account_summaries(conn: Connection) -> list[AccountSummary]:
    async with conn.cursor(row_factory=class_row(_AccountRow)) as cur:
        await cur.execute(
            """
            SELECT a.id, a.name, a.settlement_mode,
                   c.last_read,
                   (SELECT count(*) FROM settlement_exception x
                    WHERE x.account_id = a.id AND NOT EXISTS (
                      SELECT 1 FROM settlement_exception_resolved r
                      WHERE r.exception_id = x.id)) AS open_exceptions,
                   (SELECT count(*) FROM settlement_payout p
                    WHERE p.account_id = a.id
                      AND p.reconciliation = 'open') AS open_payouts
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
                id=row.id,
                name=row.name,
                settlement_mode=row.settlement_mode,
                last_read=row.last_read,
                balances=await _balances(conn, row.id),
                open_exceptions=row.open_exceptions,
                open_payouts=row.open_payouts,
            )
        )
    return found


async def account_summary(
    conn: Connection, account_id: PspAccountId
) -> AccountSummary | None:
    for summary in await account_summaries(conn):
        if summary.id == account_id:
            return summary
    return None


@dataclass(frozen=True, kw_only=True)
class PayoutRow:
    """A payout and whether its lines add up yet. `difference` is its net
    less the exact sum of its lines arrived so far, in minor units; open
    since it was stored."""

    vendor_payout_id: PspPayoutId
    currency: Currency
    net_minor: int
    paid_out_at: datetime
    recorded_at: datetime
    booked_at: datetime | None
    reconciliation: Reconciliation
    difference_numerator: int
    difference_denominator: int
    lines: int
    booked_lines: int

    @property
    def difference(self) -> Fraction:
        return Fraction(self.difference_numerator, self.difference_denominator)


async def payouts_of(
    conn: Connection, account_id: PspAccountId, limit: int
) -> list[PayoutRow]:
    """Open ones first, the longest open first; then the newest."""
    async with conn.cursor(row_factory=class_row(PayoutRow)) as cur:
        await cur.execute(
            """
            SELECT p.vendor_payout_id, p.currency, p.net_minor, p.paid_out_at,
                   p.recorded_at, p.booked_at, p.reconciliation,
                   p.difference_numerator, p.difference_denominator,
                   (SELECT count(*) FROM settlement_line l
                    WHERE l.account_id = p.account_id
                      AND l.vendor_payout_id = p.vendor_payout_id) AS lines,
                   (SELECT count(*) FROM settlement_line l
                    WHERE l.account_id = p.account_id
                      AND l.vendor_payout_id = p.vendor_payout_id
                      AND l.state = 'booked') AS booked_lines
            FROM settlement_payout p WHERE p.account_id = %s
            ORDER BY (p.reconciliation = 'open') DESC,
                     CASE WHEN p.reconciliation = 'open' THEN p.recorded_at END,
                     p.recorded_at DESC, p.id DESC
            LIMIT %s
            """,
            (account_id, limit),
        )
        return await cur.fetchall()


@dataclass(frozen=True, kw_only=True)
class ExceptionRow:
    id: SettlementExceptionId
    kind: ExceptionKind
    detail: str
    created_at: datetime
    vendor_ref: str  # the PSP's line id
    note: str | None
    resolved_by: str | None
    resolved_at: datetime | None


async def exceptions_of(
    conn: Connection, account_id: PspAccountId
) -> list[ExceptionRow]:
    """Open ones first, then the newest."""
    async with conn.cursor(row_factory=class_row(ExceptionRow)) as cur:
        await cur.execute(
            """
            SELECT x.id, x.kind, x.detail, x.created_at,
                   l.vendor_line_id AS vendor_ref,
                   r.note, r.actor_id AS resolved_by, r.resolved_at
            FROM settlement_exception x
            JOIN settlement_line l ON l.id = x.line_id
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
    account_id: PspAccountId,
    exception_id: SettlementExceptionId,
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
