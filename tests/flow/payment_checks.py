"""What the payment and order fuzzers share (docs/plan-fuzz-reach.md §3):
the rules that must hold after every action, the rules after a drain, and
the worker steps both machines take (catch up, crash inside a local step,
crash around an effect's call). Each is a plain function on the test
database's worker connection."""

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx2
from psycopg.rows import class_row

from mor.db.connect import Connection, connect
from mor.messaging import consumers
from mor.messaging.facts import AggregateType, MessageType, UndecodableFact, decode
from mor.messaging.routing import ConsumerName, Local, consumer_kind
from mor.messaging.worker import (
    Candidate,
    Failed,
    Prepared,
    find_candidates,
    handle_delivery,
    prepare_delivery,
    run_one,
    try_lock_aggregate,
)
from tests.helpers import ENV, TEST_URL, crash, effect_consumer, local_consumer


# --- after every action -------------------------------------------------------


async def check_sessions_match_their_attempts(conn: Connection) -> None:
    cur = await conn.execute(
        """
        SELECT s.id, s.state::text,
          (SELECT count(*) FROM payment_attempt a WHERE a.session_id = s.id
             AND a.state IN ('requested', 'action_required', 'authorised',
                             'cancelling')),
          (SELECT count(*) FROM payment_attempt a WHERE a.session_id = s.id
             AND a.state = 'needs_person'),
          (SELECT count(*) FROM order_ o WHERE o.session_id = s.id),
          (SELECT count(*) FROM outbox_delivery d
            JOIN payment_attempt a ON a.id = d.aggregate_id
            WHERE a.session_id = s.id AND d.handled_at IS NULL
              AND d.consumer IN ('session_payment_ended', 'order_create'))
        FROM checkout_session s
        """
    )
    for session_id, state, open_count, person, orders, ending in await cur.fetchall():
        match state:
            case "paying":
                # Or its attempt has ended and the session's own step, back
                # to quoted or on to its order, is still to run.
                assert open_count == 1 or person > 0 or ending > 0, session_id
                assert orders == 0
            case "completed":
                assert orders == 1 and open_count == 0, session_id
            case "quoted" | "quoting" | "refused" | "expired" | "open":
                assert open_count == 0 and orders == 0, (session_id, state)
            case _:
                raise AssertionError(state)


async def check_orders_are_booked(conn: Connection) -> None:
    """Each order has its one capture entry, and the captures put exactly
    the captured amounts into `psp_clearing` (settlement takes them out:
    check_settlement)."""
    cur = await conn.execute(
        """
        SELECT count(*) FROM order_ o
        WHERE NOT EXISTS (SELECT 1 FROM journal_entry e
          WHERE e.idempotency_key = 'capture:' || o.attempt_id::text)
        """
    )
    assert await cur.fetchone() == (0,)
    cur = await conn.execute(
        """
        SELECT
          (SELECT coalesce(sum(l.amount_minor), 0) FROM journal_line l
            JOIN journal_entry e ON e.id = l.entry_id
            WHERE l.account_kind = 'psp_clearing'
              AND e.idempotency_key LIKE 'capture:%'),
          (SELECT coalesce(sum(a.amount_minor), 0) FROM order_ o
            JOIN payment_attempt a ON a.id = o.attempt_id)
        """
    )
    booked, ordered = await cur.fetchone() or (None, None)
    assert booked == ordered


async def check_the_psp_charged_nothing_twice_or_unseen(conn: Connection) -> None:
    cur = await conn.execute(
        """
        SELECT c.reference, a.state::text, c.amount_minor, a.amount_minor
        FROM fake_vendor.psp_fake_charge c
        LEFT JOIN payment_attempt a ON a.id::text = c.reference
        """
    )
    references: set[str] = set()
    for reference, state, charged, amount in await cur.fetchall():
        assert reference not in references, f"{reference} charged twice"
        references.add(reference)
        assert state is not None, f"a charge for no attempt: {reference}"
        assert charged == amount, (reference, charged, amount)
        # A charge we have not heard of yet leaves the attempt open.
        assert state in (
            "captured",
            "needs_person",
            "requested",
            "action_required",
            "authorised",
            "cancelling",
        ), (reference, state)


async def check_one_psp_payment_per_attempt(conn: Connection) -> None:
    """A keyed account replays a resent create; on an unkeyed one we never
    resend. Either way one payment per reference, so a buyer can never be
    shown, or pay, a second one."""
    cur = await conn.execute(
        "SELECT reference FROM fake_vendor.psp_fake_payment"
        " GROUP BY reference HAVING count(*) > 1"
    )
    assert await cur.fetchall() == []


async def check_invoice_numbers(conn: Connection) -> None:
    """Per series 1…n, no gap and no repeat, and the counter one past the
    last; each invoice for exactly what its payment captured."""
    cur = await conn.execute(
        """
        SELECT s.code, s.next_number,
          coalesce((SELECT array_agg(i.number ORDER BY i.number)
                    FROM invoice i WHERE i.series = s.code), '{}')
        FROM invoice_series s
        """
    )
    for code, next_number, numbers in await cur.fetchall():
        assert numbers == list(range(1, len(numbers) + 1)), (code, numbers)
        assert next_number == len(numbers) + 1, (code, next_number)
    cur = await conn.execute(
        """
        SELECT i.number_text FROM invoice i
        JOIN order_ o ON o.id = i.order_id
        JOIN payment_attempt a ON a.id = o.attempt_id
        WHERE i.total_minor <> a.amount_minor
           OR i.currency <> a.currency
           OR i.total_minor <> (SELECT sum(l.amount_minor)
                                FROM invoice_line l WHERE l.invoice_id = i.id)
        """
    )
    assert await cur.fetchall() == []


async def check_settlement_is_booked_once(conn: Connection) -> None:
    """Settlement's rows and the ledger agree (docs/plan-payments.md §7.1,
    plan-settlement-reconciliation §4): a booked line has its order and its
    one entry, taking its fee out of `psp_clearing`; a booked payout put
    its net on the way to the bank; the rounding entries hold exactly the
    rounding each payout says is booked, and only a balanced payout has
    any. A line booked twice, a payout booked without its row, or a
    rounding change lost or doubled breaks one of the sums."""
    cur = await conn.execute(
        """
        SELECT l.vendor_line_id FROM settlement_line l
        WHERE l.state = 'booked' AND (
          NOT EXISTS (SELECT 1 FROM order_ o WHERE o.attempt_id = l.attempt_id)
          OR NOT EXISTS (
            SELECT 1 FROM journal_entry e WHERE e.idempotency_key =
              'settlement:' || l.account_id::text || ':' || l.vendor_line_id))
        """
    )
    assert await cur.fetchall() == []
    cur = await conn.execute(
        """
        SELECT
          (SELECT coalesce(sum(l.amount_minor), 0) FROM journal_line l
            JOIN journal_entry e ON e.id = l.entry_id
            WHERE l.account_kind = 'psp_clearing'
              AND e.idempotency_key LIKE 'settlement:%'),
          (SELECT coalesce(-sum(fee_minor), 0) FROM settlement_line
            WHERE state = 'booked'),
          (SELECT coalesce(sum(amount_minor), 0) FROM journal_line
            WHERE account_kind = 'payout_in_transit'),
          (SELECT coalesce(sum(net_minor), 0) FROM settlement_payout
            WHERE booked_at IS NOT NULL),
          (SELECT coalesce(sum(l.amount_minor), 0) FROM journal_line l
            JOIN journal_entry e ON e.id = l.entry_id
            WHERE l.account_kind = 'psp_clearing'
              AND e.idempotency_key LIKE 'payout-rounding:%'),
          (SELECT coalesce(sum(rounding_minor), 0) FROM settlement_payout),
          (SELECT count(*) FROM settlement_payout
            WHERE reconciliation = 'open' AND rounding_minor <> 0)
        """
    )
    row = await cur.fetchone()
    assert row is not None
    (
        fees_booked,
        fees_of_lines,
        in_transit,
        nets,
        rounding_booked,
        rounding_said,
        open_with_rounding,
    ) = row
    assert fees_booked == fees_of_lines, (fees_booked, fees_of_lines)
    assert in_transit == nets, (in_transit, nets)
    assert rounding_booked == rounding_said, (rounding_booked, rounding_said)
    assert open_with_rounding == 0


async def check_no_advisory_lock_is_held(conn: Connection) -> None:
    cur = await conn.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        " AND database = (SELECT oid FROM pg_database"
        " WHERE datname = current_database())"
    )
    assert await cur.fetchone() == (0,)


async def check_every_action(conn: Connection) -> None:
    await check_sessions_match_their_attempts(conn)
    await check_orders_are_booked(conn)
    await check_the_psp_charged_nothing_twice_or_unseen(conn)
    await check_one_psp_payment_per_attempt(conn)
    await check_invoice_numbers(conn)
    await check_settlement_is_booked_once(conn)
    await check_no_advisory_lock_is_held(conn)


# --- after draining with faults off ---------------------------------------------


async def check_drained(conn: Connection) -> None:
    """No attempt stuck; every charge the PSP made is booked or waits on a
    person; every order has its invoice."""
    cur = await conn.execute(
        "SELECT id, state::text FROM payment_attempt"
        " WHERE state IN ('requested', 'authorised', 'cancelling')"
    )
    stuck = await cur.fetchall()
    assert stuck == [], f"attempts stuck after the drain: {stuck}"
    cur = await conn.execute(
        """
        SELECT c.reference FROM fake_vendor.psp_fake_charge c
        JOIN payment_attempt a ON a.id::text = c.reference
        WHERE a.state NOT IN ('captured', 'needs_person')
        """
    )
    unbooked = await cur.fetchall()
    assert unbooked == [], f"charges neither booked nor with a person: {unbooked}"
    cur = await conn.execute(
        "SELECT o.id FROM order_ o"
        " WHERE NOT EXISTS (SELECT 1 FROM invoice i WHERE i.order_id = o.id)"
    )
    uninvoiced = await cur.fetchall()
    assert uninvoiced == [], f"orders without an invoice after the drain: {uninvoiced}"


# The exception each fault the fake cut a line with must give.
EXCEPTION_OF_FAULT = {
    "unknown_reference": "unknown_reference",
    "gross_differs": "amount_differs",
    "settled_again": "settled_twice",
    "unknown_type": "unknown_type",
}


async def check_settlement_drained(conn: Connection) -> None:
    """Everything the fake settled is in our books, each line once and as
    its fault says (docs/plan-payments.md §7.1): a clean line booked, a
    faulty one its exception, a line whose payment went to a person (no
    guard) `not_captured`, a balance transfer counted; every batch's
    payout booked, and open exactly where the fake cut its net off, by
    that much (plan-settlement-reconciliation §4.3). `psp_clearing` then
    holds what the orders put in, less the fees booked and the nets the
    fake paid out, plus the rounding booked (in SGD, where the fake's
    units are ours)."""
    cur = await conn.execute(
        """
        SELECT f.id, f.fault, f.type, l.state::text, x.kind::text, a.state::text
        FROM fake_vendor.psp_fake_settlement_line f
        LEFT JOIN settlement_line l ON l.vendor_line_id = f.id
        LEFT JOIN settlement_exception x ON x.line_id = l.id
        LEFT JOIN payment_attempt a ON a.id::text = f.reference
        """
    )
    for line_id, fault, line_type, state, kind, attempt_state in await cur.fetchall():
        assert state is not None, f"settled line {line_id} never stored"
        if attempt_state == "needs_person":
            assert (state, kind) == ("exception", "not_captured"), line_id
        elif line_type == "balance_transfer":
            assert (state, kind) == ("counted", None), (line_id, state, kind)
        elif fault is None:
            assert (state, kind) == ("booked", None), (line_id, state, kind)
        else:
            assert (state, kind) == ("exception", EXCEPTION_OF_FAULT[fault]), (
                line_id,
                fault,
                state,
                kind,
            )
    cur = await conn.execute(
        """
        SELECT f.id, f.fault, p.booked_at IS NOT NULL, p.reconciliation::text,
               p.difference_numerator, p.difference_denominator
        FROM fake_vendor.psp_fake_settlement_batch f
        LEFT JOIN settlement_payout p ON p.vendor_payout_id = f.id
        """
    )
    for (
        batch_id,
        fault,
        booked,
        reconciliation,
        numerator,
        denominator,
    ) in await cur.fetchall():
        assert booked, f"payout {batch_id} not booked after the drain"
        if fault == "net_differs":
            # The fake paid one dollar less than its lines.
            assert (reconciliation, numerator, denominator) == ("open", -100, 1), (
                batch_id,
                reconciliation,
                numerator,
            )
        else:
            assert reconciliation == "balanced", (batch_id, reconciliation)
    cur = await conn.execute(
        "SELECT count(*) FROM settlement_line WHERE state IN ('received', 'waiting')"
    )
    assert await cur.fetchone() == (0,)
    cur = await conn.execute(
        """
        SELECT
          (SELECT coalesce(sum(amount_minor), 0) FROM journal_line
            WHERE account_kind = 'psp_clearing'),
          (SELECT coalesce(sum(a.amount_minor), 0) FROM order_ o
            JOIN payment_attempt a ON a.id = o.attempt_id)
          - (SELECT coalesce(sum(fee_minor), 0) FROM settlement_line
              WHERE state = 'booked')
          - (SELECT coalesce(sum(net_minor), 0)
              FROM fake_vendor.psp_fake_settlement_batch)
          + (SELECT coalesce(sum(rounding_minor), 0) FROM settlement_payout)
        """
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == row[1], row


# --- worker steps -----------------------------------------------------------------


async def catch_up(worker: Connection, http: httpx2.AsyncClient, now: datetime) -> None:
    for _ in range(100):
        candidates = await find_candidates(worker, now, 50)
        if not candidates:
            return
        for candidate in candidates:
            result = await run_one(worker, http, ENV, candidate, now)
            assert not isinstance(result, Failed), result
    raise AssertionError("the worker never caught up")


class _CrashBeforeCommit(Exception):
    """Ends a local step's transaction the way a crash would: unfinished."""


async def crash_in_a_local_step(candidate: Candidate, now: datetime) -> None:
    """A local consumer's work done, and the worker gone before its one
    commit: nothing of it may stay (an invoice number taken, say)."""
    crashing = await connect(TEST_URL, autocommit=True)
    try:
        try:
            async with crashing.transaction():
                await handle_delivery(
                    crashing, candidate, local_consumer(candidate), ENV, now
                )
                raise _CrashBeforeCommit
        except _CrashBeforeCommit:
            pass
    finally:
        await crash(crashing)


async def crash_around_a_call(
    candidate: Candidate, with_call: bool, http: httpx2.AsyncClient, now: datetime
) -> None:
    """An effect's `prepare` committed, then the worker gone, before its
    call or after it."""
    crashing = await connect(TEST_URL, autocommit=True)
    try:
        assert await try_lock_aggregate(crashing, candidate)
        async with crashing.transaction():
            prepared = await prepare_delivery(
                crashing, candidate, effect_consumer(candidate), now
            )
        if with_call and isinstance(prepared, Prepared):
            await consumers.call(http, ENV, prepared.call)
    finally:
        await crash(crashing)


@dataclass(frozen=True, kw_only=True)
class Handled:
    """A handled delivery and its fact, as stored."""

    message_id: uuid.UUID
    consumer: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    message_type: str
    schema_version: int
    payload: str


async def handled_locally(worker: Connection) -> list[Handled]:
    """Every handled delivery of a local consumer, oldest first."""
    async with worker.cursor(row_factory=class_row(Handled)) as cur:
        await cur.execute(
            """
            SELECT d.message_id, d.consumer::text AS consumer,
                   o.aggregate_type::text AS aggregate_type, o.aggregate_id,
                   o.message_type::text AS message_type, o.schema_version,
                   o.payload::text AS payload
            FROM outbox_delivery d JOIN outbox o ON o.id = d.message_id
            WHERE d.handled_at IS NOT NULL ORDER BY o.seq, d.consumer
            """
        )
        rows = await cur.fetchall()
    found: list[Handled] = []
    for row in rows:
        if isinstance(consumer_kind(ConsumerName(row.consumer)), Local):
            found.append(row)
    return found


async def meet_again(chosen: Handled, now: datetime) -> None:
    """A handled fact handed to its local consumer once more, past the
    inbox, and committed, as a broker's redelivery could. A consumer may
    emit its facts again (decoding a stored webhook does, by design); the
    guard downstream must hold, which the rules and the drain check."""
    fact = decode(
        MessageType(chosen.message_type),
        chosen.schema_version,
        chosen.payload.encode(),
    )
    assert not isinstance(fact, UndecodableFact), fact
    candidate = Candidate(
        message_id=chosen.message_id,
        consumer=ConsumerName(chosen.consumer),
        aggregate_type=AggregateType(chosen.aggregate_type),
        aggregate_id=chosen.aggregate_id,
    )
    conn = await connect(TEST_URL, autocommit=True)
    try:
        async with conn.transaction():
            await consumers.handle(conn, local_consumer(candidate), fact, ENV, now)
    finally:
        await crash(conn)
