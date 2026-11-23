"""Settlement in the database (docs/plan-payments.md §7.1): stored reports
decoded into batches and lines, each once by the PSP's id; each line booked
against its capture; each batch's payout booked on its way to our bank;
what does not fit kept for a person.

    psp_inbox_decode (per account) ─▶ record_document: batch and lines,
        one settlement_line_received per new line, and on a batch's last
        page settlement_batch_received
    settlement_line_book (per line, and per attempt on order_paid or a
        payment's end): under the attempt's lock, every line of the attempt
        not settled yet, earliest first: book the fee, wait for the order,
        or record an exception
    settlement_payout_book (per batch): the payout, checked exactly

Persistence functions take the connection first and never commit.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

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
    SettlementBatchReceivedV1,
    SettlementLineReceivedV1,
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
from mor.psp.port import (
    LineType,
    PspAccount,
    SettlementMode,
)
from mor.settlement.entries import (
    ROUNDING,
    LineAmounts,
    NetDiffers,
    check_net,
    line_entry,
    payout_entry,
    to_minor,
)
from mor.settlement.records import ExceptionKind, LineState
from mor.vendor_failures import UndecodableBody


DECODER = Actor(ActorKind.SYSTEM, "psp_inbox_decode")
LINE_BOOKER = Actor(ActorKind.SYSTEM, "settlement_line_book")
PAYOUT_BOOKER = Actor(ActorKind.SYSTEM, "settlement_payout_book")

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
    mode: SettlementMode,
    body: bytes,
    now: datetime,
) -> UndecodableBody | None:
    """The batch and its lines, each inserted once by the PSP's own id; a
    fact for each line new here, and for the batch once its last page is
    in. A page decoded twice adds nothing."""
    document = registry.decode_settlement(account, mode, body)
    if isinstance(document, UndecodableBody):
        return document
    batch = document.batch
    cur = await conn.execute(
        """
        INSERT INTO settlement_batch
          (id, account_id, vendor_batch_id, currency, net_numerator,
           net_denominator, net_minor, paid_out_at, inbox_id, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (account_id, vendor_batch_id) DO NOTHING
        """,
        (
            uuid.uuid7(),
            account.id,
            batch.vendor_batch_id,
            batch.currency.value,
            batch.net.numerator,
            batch.net.denominator,
            to_minor(batch.net),
            batch.paid_out_at,
            inbox_id,
            now,
        ),
    )
    batch_id = await one_value(
        conn,
        "SELECT id FROM settlement_batch"
        " WHERE account_id = %s AND vendor_batch_id = %s",
        (account.id, batch.vendor_batch_id),
        uuid.UUID,
    )
    for line in document.lines:
        # The attempt a reference names never changes: kept from the start,
        # so an attempt's lines are booked in the order the PSP sent them.
        attempt_id: uuid.UUID | None = None
        if line.line_type == LineType.CAPTURE:
            attempt_id = await attempt_of_account(conn, account.id, line.reference)
        cur = await conn.execute(
            """
            INSERT INTO settlement_line
              (id, batch_id, account_id, vendor_line_id, type, vendor_type,
               provider_payment_id, our_reference, currency, gross_numerator,
               gross_denominator, gross_minor, fee_numerator, fee_denominator,
               fee_minor, rounding, attempt_id, state, recorded_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, vendor_line_id) DO NOTHING
            RETURNING id
            """,
            (
                uuid.uuid7(),
                batch_id,
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
    if document.last:
        await append_fact(
            conn,
            SettlementBatchReceivedV1(
                settlement_batch_id=batch_id, psp_account_id=account.id
            ),
            DECODER,
            now,
        )
    return None


# --- booking a line ---------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    id: uuid.UUID
    batch_id: uuid.UUID
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
    batch_currency: Currency
    paid_out_at: datetime


_LINE_SELECT = """
    SELECT l.id, l.batch_id, l.account_id, l.vendor_line_id, l.type,
           l.vendor_type, l.provider_payment_id, l.our_reference,
           l.currency, l.gross_numerator, l.gross_denominator, l.fee_minor,
           l.state, b.currency AS batch_currency, b.paid_out_at
    FROM settlement_line l JOIN settlement_batch b ON b.id = l.batch_id
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
    if line.currency != currency or line.batch_currency != currency:
        await _exception(
            conn,
            line,
            ExceptionKind.CURRENCY_DIFFERS,
            f"line in {line.currency}, batch in {line.batch_currency},"
            f" payment in {currency.value}",
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
        line.paid_out_at,
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
    if line.type == LineType.UNKNOWN:
        await _exception(
            conn,
            line,
            ExceptionKind.UNKNOWN_TYPE,
            f"a {line.vendor_type!r} line is not booked",
            now,
        )
    else:
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


# --- booking a payout ---------------------------------------------------------------


@dataclass(frozen=True)
class _BatchRow:
    account_id: uuid.UUID
    vendor_batch_id: str
    currency: Currency
    net_numerator: int
    net_denominator: int
    paid_out_at: datetime
    booked_at: datetime | None


@dataclass(frozen=True)
class _LineAmountsRow:
    gross_numerator: int
    gross_denominator: int
    fee_numerator: int
    fee_denominator: int


async def book_payout(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `settlement_payout_book`: the net the PSP says it paid, on
    its way to our bank, once per batch under the batch's lock."""
    match fact:
        case SettlementBatchReceivedV1(settlement_batch_id=batch_id):
            pass
        case _:
            raise WrongFact(f"settlement_payout_book got {fact!r}")
    async with conn.cursor(row_factory=class_row(_BatchRow)) as cur:
        await cur.execute(
            """
            SELECT account_id, vendor_batch_id, currency, net_numerator,
                   net_denominator, paid_out_at, booked_at
            FROM settlement_batch WHERE id = %s FOR UPDATE
            """,
            (batch_id,),
        )
        batch = await one_row(cur, f"settlement batch {batch_id}")
    if batch.booked_at is not None:
        return Stale()
    account_id = batch.account_id
    currency = batch.currency
    async with conn.cursor(row_factory=class_row(_LineAmountsRow)) as cur:
        await cur.execute(
            """
            SELECT gross_numerator, gross_denominator, fee_numerator,
                   fee_denominator
            FROM settlement_line WHERE batch_id = %s AND currency = %s
            """,
            (batch_id, currency),
        )
        line_rows = await cur.fetchall()
    amounts: list[LineAmounts] = []
    for line_row in line_rows:
        amounts.append(
            LineAmounts(
                gross=Fraction(line_row.gross_numerator, line_row.gross_denominator),
                fee=Fraction(line_row.fee_numerator, line_row.fee_denominator),
            )
        )
    reported = Fraction(batch.net_numerator, batch.net_denominator)
    check = check_net(reported, amounts)
    entry = payout_entry(
        account_id=account_id,
        vendor_batch_id=batch.vendor_batch_id,
        currency=currency,
        net=to_minor(reported),
        check=check,
        effective_at=batch.paid_out_at,
        actor=PAYOUT_BOOKER,
        now=now,
    )
    if entry is not None:
        await record_entry(conn, entry)
    if isinstance(check, NetDiffers):
        await conn.execute(
            """
            INSERT INTO settlement_exception
              (id, account_id, batch_id, kind, detail, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (batch_id) DO NOTHING
            """,
            (
                uuid.uuid7(),
                account_id,
                batch_id,
                ExceptionKind.NET_DIFFERS,
                f"net {check.reported} {currency} minor, its lines add up to"
                f" {check.expected}",
                now,
            ),
        )
    await conn.execute(
        "UPDATE settlement_batch SET booked_at = %s WHERE id = %s", (now, batch_id)
    )
    return Applied()
