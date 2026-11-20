"""What the payment and order fuzzers share (docs/plan-fuzz-reach.md §3):
the rules that must hold after every action, the rules after a drain, and
the worker steps both machines take (catch up, crash inside a local step,
crash around an effect's call). Each is a plain function on the test
database's worker connection."""

from datetime import datetime

import httpx2

from mor.db.connect import Connection, connect
from mor.messaging import consumers
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
from tests.helpers import ENV, TEST_URL, crash


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
    """Each order has its one capture entry, and `psp_clearing` holds
    exactly the captured amounts."""
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
          (SELECT coalesce(sum(amount_minor), 0) FROM journal_line
            WHERE account_kind = 'psp_clearing'),
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
        assert await try_lock_aggregate(crashing, candidate)
        try:
            async with crashing.transaction():
                await handle_delivery(crashing, candidate, ENV, now)
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
            prepared = await prepare_delivery(crashing, candidate, now)
        if with_call and isinstance(prepared, Prepared):
            await consumers.call(http, ENV, prepared.call)
    finally:
        await crash(crashing)
