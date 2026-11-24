"""Settlement in the database (docs/plan-payments.md §7.1,
docs/plan-settlement-reconciliation.md §4): stored documents decoded into
payouts and lines, each once by the PSP's id and in any order; each line
booked against its capture; each payout booked on its way to our bank as
reported; each payout reconciled against whatever lines have arrived; what
does not fit kept for a person.

    psp_inbox_decode (per account) ─▶ record_document: a payout, lines or
        both; settlement_payout_reported per new payout,
        settlement_line_received per new line
    settlement_line_book (per line, and per attempt on order_paid or a
        payment's end): under the attempt's lock, every line of the attempt
        not settled yet, earliest first: book the fee, wait for the order,
        or record an exception
    settlement_payout_book (per payout): its net as reported
    settlement_payout_reconcile (on a payout or any of its lines): under
        the payout's lock, its net against the lines arrived so far

Persistence functions take the connection first and never commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import LiteralString

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.db.fetch import one_row, one_value
from mor.ledger.account import AccountKind
from mor.ledger.store import record_entry
from mor.messaging.facts import (
    Fact,
    OrderPaidV1,
    PaymentCancelledV1,
    PaymentDeclinedV1,
    PaymentEventsWantedV1,
    PaymentExpiredV1,
    PaymentNeedsPersonV1,
    SettlementLineReceivedV1,
    SettlementPayoutReportedV1,
)
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.routing import WrongFact
from mor.messaging.store import append_fact
from mor.money.amount import Money
from mor.money.currency import Currency
from mor.orders.posting import capture_key
from mor.payments.attempts import Attempt, attempt_of_account, lock_attempt
from mor.payments.transitions import OPEN_STATES, AttemptState
from mor.psp import registry
from mor.psp.port import LineType, PspAccount
from mor.psp.registry import SettlementInbound
from mor.settlement.entries import (
    ROUNDING,
    AddedUp,
    LineAmounts,
    NetDiffers,
    check_net,
    line_entry,
    payout_entry,
    rounding_entry,
    to_minor,
)
from mor.settlement.records import ExceptionKind, LineState, Reconciliation
from mor.vendor_failures import UndecodableBody


DECODER = Actor(ActorKind.SYSTEM, "psp_inbox_decode")
LINE_BOOKER = Actor(ActorKind.SYSTEM, "settlement_line_book")
PAYOUT_BOOKER = Actor(ActorKind.SYSTEM, "settlement_payout_book")
RECONCILER = Actor(ActorKind.SYSTEM, "settlement_payout_reconcile")

ENDED_OTHERWISE = (
    AttemptState.DECLINED,
    AttemptState.EXPIRED,
    AttemptState.CANCELLED,
    AttemptState.NEEDS_PERSON,
)


# --- decoding a stored report or page -------------------------------------------


async def record_document(
    conn: Connection,
    account: PspAccount,
    inbox_id: uuid.UUID,
    mode: SettlementInbound,
    body: bytes,
    request_ref: str | None,
    now: datetime,
) -> UndecodableBody | None:
    """The payout and the lines a document holds, each inserted once by the
    PSP's own id, with a fact for each one new here. Nothing waits for
    another document: a line can come before its payout, and pages in any
    order. A document decoded twice adds nothing."""
    document = registry.decode_settlement(account, mode, body, request_ref)
    if isinstance(document, UndecodableBody):
        return document
    payout = document.payout
    if payout is not None:
        cur = await conn.execute(
            """
            INSERT INTO settlement_payout
              (id, account_id, vendor_payout_id, currency, net_numerator,
               net_denominator, net_minor, paid_out_at, inbox_id, recorded_at,
               reconciliation, difference_numerator, difference_denominator,
               rounding_minor, rounding_entries)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0)
            ON CONFLICT (account_id, vendor_payout_id) DO NOTHING
            RETURNING id
            """,
            (
                uuid.uuid7(),
                account.id,
                payout.vendor_payout_id,
                payout.currency.value,
                payout.net.numerator,
                payout.net.denominator,
                to_minor(payout.net),
                payout.paid_out_at,
                inbox_id,
                now,
                Reconciliation.OPEN,
                payout.net.numerator,
                payout.net.denominator,
            ),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            await append_fact(
                conn,
                SettlementPayoutReportedV1(
                    settlement_payout_id=inserted[0], psp_account_id=account.id
                ),
                DECODER,
                now,
            )
    for line in document.lines:
        # The attempt a reference names never changes: kept from the start,
        # so an attempt's lines are booked in the order they were stored.
        attempt_id: uuid.UUID | None = None
        if line.line_type == LineType.CAPTURE:
            attempt_id = await attempt_of_account(conn, account.id, line.reference)
        cur = await conn.execute(
            """
            INSERT INTO settlement_line
              (id, vendor_payout_id, account_id, vendor_line_id, type,
               vendor_type, provider_payment_id, our_reference, currency,
               gross_numerator, gross_denominator, gross_minor, fee_numerator,
               fee_denominator, fee_minor, rounding, attempt_id, state,
               settled_at, recorded_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, vendor_line_id) DO NOTHING
            RETURNING id
            """,
            (
                uuid.uuid7(),
                line.vendor_payout_id,
                account.id,
                line.vendor_line_id,
                line.line_type.value,
                line.vendor_type,
                line.provider_payment_id,
                line.reference,
                line.currency.value,
                line.gross.numerator,
                line.gross.denominator,
                to_minor(line.gross),
                line.fee.numerator,
                line.fee.denominator,
                to_minor(line.fee),
                ROUNDING.value,
                attempt_id,
                LineState.RECEIVED,
                line.settled_at,
                now,
                now,
            ),
        )
        inserted = await cur.fetchone()
        if inserted is not None:
            await append_fact(
                conn,
                SettlementLineReceivedV1(
                    settlement_line_id=inserted[0], psp_account_id=account.id
                ),
                DECODER,
                now,
            )
    return None


# --- booking a line ---------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class _Line:
    id: uuid.UUID
    account_id: uuid.UUID
    vendor_line_id: str
    type: LineType
    vendor_type: str
    provider_payment_id: str
    our_reference: str
    currency: Currency
    gross_numerator: int
    gross_denominator: int
    fee_minor: int
    state: LineState
    settled_at: datetime


_LINE_SELECT = """
    SELECT l.id, l.account_id, l.vendor_line_id, l.type, l.vendor_type,
           l.provider_payment_id, l.our_reference, l.currency,
           l.gross_numerator, l.gross_denominator, l.fee_minor, l.state,
           l.settled_at
    FROM settlement_line l
"""


async def _lock_line(conn: Connection, line_id: uuid.UUID) -> _Line:
    async with conn.cursor(row_factory=class_row(_Line)) as cur:
        await cur.execute(_LINE_SELECT + " WHERE l.id = %s FOR UPDATE OF l", (line_id,))
        return await one_row(cur, f"settlement line {line_id}")


async def _lock_pending_lines(conn: Connection, attempt_id: uuid.UUID) -> list[_Line]:
    """An attempt's lines not settled yet, in the order they were stored,
    which is the order the PSP published them."""
    async with conn.cursor(row_factory=class_row(_Line)) as cur:
        await cur.execute(
            _LINE_SELECT + " WHERE l.attempt_id = %s"
            " AND l.state IN ('received', 'waiting')"
            " ORDER BY l.id FOR UPDATE OF l",
            (attempt_id,),
        )
        return await cur.fetchall()


async def _exception(
    conn: Connection,
    line: _Line,
    kind: ExceptionKind,
    detail: str,
    now: datetime,
) -> None:
    await conn.execute(
        """
        INSERT INTO settlement_exception
          (id, account_id, line_id, kind, detail, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (line_id) DO NOTHING
        """,
        (uuid.uuid7(), line.account_id, line.id, kind, detail, now),
    )
    await conn.execute(
        "UPDATE settlement_line SET state = %s, updated_at = %s WHERE id = %s",
        (LineState.EXCEPTION, now, line.id),
    )


async def _estimate(
    conn: Connection, attempt_id: uuid.UUID, currency: Currency
) -> Money:
    """The PSP fee the capture booked as collected: what was booked, not
    recomputed from the quote."""
    collected = await one_value(
        conn,
        """
        SELECT coalesce(sum(l.amount_minor), 0)::bigint
        FROM journal_line l JOIN journal_entry e ON e.id = l.entry_id
        WHERE e.idempotency_key = %s AND l.account_kind = %s AND l.currency = %s
        """,
        (capture_key(attempt_id), AccountKind.PSP_FEE_COLLECTED.value, currency.value),
        int,
    )
    return Money(-collected, currency)  # collected is a credit


async def _settle(
    conn: Connection, line: _Line, attempt: Attempt, now: datetime
) -> None:
    """One capture line against its attempt, both locked: book it, wait for
    the order, or record why not."""
    if line.provider_payment_id != attempt.provider_payment_id:
        await _exception(
            conn,
            line,
            ExceptionKind.UNKNOWN_REFERENCE,
            f"payment {line.provider_payment_id!r} is not the PSP's id for"
            f" attempt {attempt.id} ({attempt.provider_payment_id!r})",
            now,
        )
        return
    currency = attempt.amount.currency
    if line.currency != currency:
        await _exception(
            conn,
            line,
            ExceptionKind.CURRENCY_DIFFERS,
            f"line in {line.currency}, payment in {currency.value}",
            now,
        )
        return
    if attempt.state in ENDED_OTHERWISE:
        await _exception(
            conn,
            line,
            ExceptionKind.NOT_CAPTURED,
            f"attempt {attempt.id} is {attempt.state.value}",
            now,
        )
        return
    gross = Fraction(line.gross_numerator, line.gross_denominator)
    if gross != attempt.amount.amount_minor:
        await _exception(
            conn,
            line,
            ExceptionKind.AMOUNT_DIFFERS,
            f"gross {gross} {currency.value} minor, captured"
            f" {attempt.amount.amount_minor}",
            now,
        )
        return
    cur = await conn.execute(
        """
        SELECT vendor_line_id FROM settlement_line
        WHERE attempt_id = %s AND type = 'capture' AND state = 'booked' AND id <> %s
        """,
        (attempt.id, line.id),
    )
    earlier = await cur.fetchone()
    if earlier is not None:
        await _exception(
            conn,
            line,
            ExceptionKind.SETTLED_TWICE,
            f"attempt {attempt.id} was settled by line {earlier[0]}",
            now,
        )
        return
    cur = await conn.execute(
        "SELECT 1 FROM order_ WHERE attempt_id = %s", (attempt.id,)
    )
    if await cur.fetchone() is None:
        # Settlement got ahead of our books. The order's making, or the
        # payment's end, books this line or turns it into an exception.
        if line.state != LineState.WAITING:
            await conn.execute(
                "UPDATE settlement_line SET state = %s, updated_at = %s WHERE id = %s",
                (LineState.WAITING, now, line.id),
            )
            if attempt.state in OPEN_STATES:
                await append_fact(
                    conn,
                    PaymentEventsWantedV1(payment_attempt_id=attempt.id),
                    LINE_BOOKER,
                    now,
                )
        return
    estimate = await _estimate(conn, attempt.id, currency)
    entry = line_entry(
        line.account_id,
        line.vendor_line_id,
        estimate,
        Money(line.fee_minor, currency),
        line.settled_at,
        LINE_BOOKER,
        now,
    )
    if entry is not None:
        await record_entry(conn, entry)
    await conn.execute(
        "UPDATE settlement_line SET state = %s, updated_at = %s WHERE id = %s",
        (LineState.BOOKED, now, line.id),
    )


@dataclass(frozen=True)
class _LineStateRow:
    attempt_id: uuid.UUID | None
    state: LineState


async def _book_received(
    conn: Connection, line_id: uuid.UUID, now: datetime
) -> ConsumerOutcome:
    async with conn.cursor(row_factory=class_row(_LineStateRow)) as cur:
        await cur.execute(
            "SELECT attempt_id, state FROM settlement_line WHERE id = %s",
            (line_id,),
        )
        found = await one_row(cur, f"settlement line {line_id}")
    if found.state != LineState.RECEIVED:
        return Stale()
    if found.attempt_id is not None:
        return await _settle_pending(conn, found.attempt_id, now)
    line = await _lock_line(conn, line_id)
    if line.state != LineState.RECEIVED:
        return Stale()
    match line.type:
        case LineType.BALANCE_TRANSFER:
            # Money moved between the PSP's batches: nothing to book, its
            # payout's reconciliation counts it.
            await conn.execute(
                "UPDATE settlement_line SET state = %s, updated_at = %s WHERE id = %s",
                (LineState.COUNTED, now, line.id),
            )
        case LineType.UNKNOWN:
            await _exception(
                conn,
                line,
                ExceptionKind.UNKNOWN_TYPE,
                f"a {line.vendor_type!r} line is not booked",
                now,
            )
        case LineType.CAPTURE:
            # Its attempt was looked up when it was stored, and none had
            # its reference.
            await _exception(
                conn,
                line,
                ExceptionKind.UNKNOWN_REFERENCE,
                f"no payment of this account has reference {line.our_reference!r}",
                now,
            )
    return Applied()


async def _settle_pending(
    conn: Connection, attempt_id: uuid.UUID, now: datetime
) -> ConsumerOutcome:
    """Every line of the attempt not settled yet, the earliest first, under
    the attempt's lock: whichever delivery comes first, a payment the PSP
    settled twice keeps its first line and the later one is the exception
    (found by the order fuzzer, 20-Nov-2026)."""
    attempt = await lock_attempt(conn, attempt_id)
    lines = await _lock_pending_lines(conn, attempt_id)
    if not lines:
        return Stale()
    for line in lines:
        await _settle(conn, line, attempt, now)
    return Applied()


async def book_lines(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `settlement_line_book`: a line as it arrives, or the lines
    that waited for their payment once it has its order or has ended."""
    match fact:
        case SettlementLineReceivedV1(settlement_line_id=line_id):
            return await _book_received(conn, line_id, now)
        case OrderPaidV1(order_id=order_id):
            attempt_id = await one_value(
                conn,
                "SELECT attempt_id FROM order_ WHERE id = %s",
                (order_id,),
                uuid.UUID,
            )
            return await _settle_pending(conn, attempt_id, now)
        case (
            PaymentDeclinedV1(payment_attempt_id=attempt_id)
            | PaymentExpiredV1(payment_attempt_id=attempt_id)
            | PaymentCancelledV1(payment_attempt_id=attempt_id)
            | PaymentNeedsPersonV1(payment_attempt_id=attempt_id)
        ):
            return await _settle_pending(conn, attempt_id, now)
        case _:
            raise WrongFact(f"settlement_line_book got {fact!r}")


# --- booking and reconciling a payout -----------------------------------------


@dataclass(frozen=True, kw_only=True)
class _PayoutRow:
    id: uuid.UUID
    account_id: uuid.UUID
    vendor_payout_id: str
    currency: Currency
    net_numerator: int
    net_denominator: int
    paid_out_at: datetime
    booked_at: datetime | None
    rounding_minor: int
    rounding_entries: int


_PAYOUT_SELECT = """
    SELECT id, account_id, vendor_payout_id, currency, net_numerator,
           net_denominator, paid_out_at, booked_at, rounding_minor,
           rounding_entries
    FROM settlement_payout
"""


async def book_payout(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `settlement_payout_book`: the net the PSP says it paid, on
    its way to our bank, as reported and once, under the payout's lock.
    Whether its lines add up is the reconciliation's."""
    match fact:
        case SettlementPayoutReportedV1(settlement_payout_id=payout_id):
            pass
        case _:
            raise WrongFact(f"settlement_payout_book got {fact!r}")
    async with conn.cursor(row_factory=class_row(_PayoutRow)) as cur:
        await cur.execute(_PAYOUT_SELECT + " WHERE id = %s FOR UPDATE", (payout_id,))
        payout = await one_row(cur, f"settlement payout {payout_id}")
    if payout.booked_at is not None:
        return Stale()
    net = Fraction(payout.net_numerator, payout.net_denominator)
    entry = payout_entry(
        payout.account_id,
        payout.vendor_payout_id,
        Money(to_minor(net), payout.currency),
        payout.paid_out_at,
        PAYOUT_BOOKER,
        now,
    )
    if entry is not None:
        await record_entry(conn, entry)
    await conn.execute(
        "UPDATE settlement_payout SET booked_at = %s WHERE id = %s", (now, payout.id)
    )
    return Applied()


@dataclass(frozen=True)
class _PayoutKey:
    account_id: uuid.UUID
    vendor_payout_id: str


async def _payout_key(conn: Connection, fact: Fact) -> _PayoutKey:
    """Which payout a fact is about, by the PSP's id: a line names it before
    the payout may be stored."""
    match fact:
        case SettlementLineReceivedV1(settlement_line_id=line_id):
            query: LiteralString = (
                "SELECT account_id, vendor_payout_id FROM settlement_line WHERE id = %s"
            )
            row_id = line_id
        case SettlementPayoutReportedV1(settlement_payout_id=payout_id):
            query = (
                "SELECT account_id, vendor_payout_id FROM settlement_payout"
                " WHERE id = %s"
            )
            row_id = payout_id
        case _:
            raise WrongFact(f"settlement_payout_reconcile got {fact!r}")
    async with conn.cursor(row_factory=class_row(_PayoutKey)) as cur:
        await cur.execute(query, (row_id,))
        return await one_row(cur, f"settlement row {row_id}")


async def reconcile_payout(
    conn: Connection, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """Consumer `settlement_payout_reconcile`, on a payout and on each of its
    lines: under the payout's lock, does its net add up to the lines arrived
    so far? Every stored line sends its fact after its commit, so the last
    run sees them all, whatever the order (plan-settlement-reconciliation
    §4.2). The booked rounding follows: a new entry for each change, never
    an edit."""
    key = await _payout_key(conn, fact)
    async with conn.cursor(row_factory=class_row(_PayoutRow)) as cur:
        await cur.execute(
            _PAYOUT_SELECT
            + " WHERE account_id = %s AND vendor_payout_id = %s FOR UPDATE",
            (key.account_id, key.vendor_payout_id),
        )
        payout = await cur.fetchone()
    if payout is None:
        return Stale()  # a line first: its payout's own fact runs this again
    async with conn.cursor(row_factory=class_row(_LineAmountsRow)) as cur:
        await cur.execute(
            """
            SELECT gross_numerator, gross_denominator, fee_numerator,
                   fee_denominator
            FROM settlement_line
            WHERE account_id = %s AND vendor_payout_id = %s AND currency = %s
            """,
            (payout.account_id, payout.vendor_payout_id, payout.currency),
        )
        line_rows = await cur.fetchall()
    amounts: list[LineAmounts] = []
    exact = Fraction(0)
    for line_row in line_rows:
        gross = Fraction(line_row.gross_numerator, line_row.gross_denominator)
        fee = Fraction(line_row.fee_numerator, line_row.fee_denominator)
        amounts.append(LineAmounts(gross=gross, fee=fee))
        exact += gross - fee
    reported = Fraction(payout.net_numerator, payout.net_denominator)
    reconciliation = Reconciliation.OPEN
    target = 0
    match check_net(reported, amounts):
        case AddedUp(rounding=rounding):
            reconciliation = Reconciliation.BALANCED
            target = rounding
        case NetDiffers():
            pass
    rounding_entries = payout.rounding_entries
    change = target - payout.rounding_minor
    if change != 0:
        rounding_entries += 1
        entry = rounding_entry(
            payout.account_id,
            payout.vendor_payout_id,
            rounding_entries,
            Money(change, payout.currency),
            payout.paid_out_at,
            RECONCILER,
            now,
        )
        assert entry is not None  # a change is never zero here
        await record_entry(conn, entry)
    difference = reported - exact
    await conn.execute(
        """
        UPDATE settlement_payout
        SET reconciliation = %s, difference_numerator = %s,
            difference_denominator = %s, rounding_minor = %s,
            rounding_entries = %s, reconciled_at = %s
        WHERE id = %s
        """,
        (
            reconciliation,
            difference.numerator,
            difference.denominator,
            target,
            rounding_entries,
            now,
            payout.id,
        ),
    )
    return Applied()


@dataclass(frozen=True, kw_only=True)
class _LineAmountsRow:
    gross_numerator: int
    gross_denominator: int
    fee_numerator: int
    fee_denominator: int
