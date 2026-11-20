"""The payment flow as a Hypothesis state machine against mor_test, the
fake PSP and the worker (docs/plan-payments.md §8). Each run draws the
account's wiring: how news arrives (fat or thin webhooks, polling), how it
captures (sale, or authorise then capture) and its duplicate guard.

Actions: the buyer pays (also twice at once), pays another way, approves,
declines or lets a payment expire on the PSP's side; the PSP pushes again
every event, newest first; run a delivery; the worker catches up; crash
after an effect's `prepare`, or after its call; script a PSP fault; refuse
the next connection; move the clock, now and then past a quote's hold, a
QR's life or a session's.

After every action: the PSP made at most one payment per attempt; a
session's state matches its attempts (paying has an
open attempt or one waiting on a person, completed has its order, any
other state has no open attempt); each order has its one capture entry,
and `psp_clearing` holds exactly the captured amounts; the PSP charged no
reference twice, and every charge is an attempt we captured or handed to
a person; no advisory lock is left held.
After draining with faults off: no attempt is stuck, only those waiting
on the buyer are still open, and every charge the PSP made is booked or
waits on a person.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from datetime import timedelta

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from mor.checkout.flow import QUOTE_VALID_FOR, refresh_quote
from mor.checkout.records import SESSION_LIFETIME
from mor.db.connect import Connection, connect
from mor.fakes.psp import QR_LIFETIME
from mor.messaging import consumers
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import consumer_kind
from mor.messaging.worker import (
    Candidate,
    Failed,
    Prepared,
    find_candidates,
    prepare_delivery,
    run_one,
    try_lock_aggregate,
)
from mor.payments.flow import pay, pay_another_way
from mor.psp.port import CaptureMode, StatusMode
from tests.helpers import ENV, TEST_URL, crash, empty_tables, wire
from tests.payments.support import (
    ACTOR,
    NOW,
    SGD_PRICE,
    Wiring,
    make_shop,
    quoted_session,
)


T0 = NOW
FAULTS = ("fail_500", "fail_500_after_work", "timeout_after_work", "decline")
OPERATIONS = ("create", "capture", "cancel", "get")


class PaymentFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        self.now = T0
        wired = wire(TEST_URL, lambda: self.now)
        self.network = wired.network
        self.http = wired.http
        self.control = wired.control
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.sessions: list[uuid.UUID] = []
        self.options: dict[uuid.UUID, str] = {}

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            if self.sessions:
                self.run(self._drain_and_check())
        finally:
            self.run(self.http.aclose())
            self.run(self.control.aclose())
            self.run(self.worker.close())
            self.loop.close()

    # --- actions ---------------------------------------------------------

    @initialize(
        status_mode=st.sampled_from(list(StatusMode)),
        capture_mode=st.sampled_from(list(CaptureMode)),
        guard=st.sampled_from((DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)),
    )
    def a_shop_with_two_quoted_sessions(
        self, status_mode: StatusMode, capture_mode: CaptureMode, guard: DuplicateGuard
    ) -> None:
        async def step() -> None:
            shop = await make_shop(
                TEST_URL, self.control, Wiring(status_mode, capture_mode, guard)
            )
            for _ in range(2):
                session_id, option = await quoted_session(
                    TEST_URL, shop.merchant_id, SGD_PRICE, self.now
                )
                self.sessions.append(session_id)
                self.options[session_id] = option

        self.run(step())

    @rule(
        session=st.integers(0, 1),
        method=st.sampled_from(("card", "qr")),
        twice=st.booleans(),
    )
    def the_buyer_pays(self, session: int, method: str, twice: bool) -> None:
        session_id = self.sessions[session]

        async def one() -> None:
            async with await connect(TEST_URL) as conn:
                await pay(
                    conn,
                    session_id,
                    self.options[session_id],
                    method,
                    "http://mor/c/back",
                    ACTOR,
                    self.now,
                )
                await conn.commit()

        async def step() -> None:
            if twice:
                await asyncio.gather(one(), one())
            else:
                await one()

        self.run(step())

    @rule(session=st.integers(0, 1))
    def the_buyer_pays_another_way(self, session: int) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await pay_another_way(conn, self.sessions[session], ACTOR, self.now)
                await conn.commit()

        self.run(step())

    @rule(session=st.integers(0, 1))
    def the_buyer_asks_for_the_current_price(self, session: int) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await refresh_quote(conn, self.sessions[session], ENV, self.now)
                await conn.commit()

        self.run(step())

    @rule(
        data=st.data(),
        outcome=st.sampled_from(("paid", "paid", "declined", "expired")),
        push=st.sampled_from(("once", "twice", "bad_signature")),
    )
    def the_buyer_answers_at_the_psp(
        self, data: st.DataObject, outcome: str, push: str
    ) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT id FROM fake_vendor.psp_fake_payment"
                " WHERE status = 'requires_action' ORDER BY id"
            )
        )
        waiting = [row[0] for row in self.run(cur.fetchall())]
        if not waiting:
            return
        payment_id = data.draw(st.sampled_from(waiting))
        response = self.run(
            self.control.post(
                f"http://mor/_fake/psp/acct/payments/{payment_id}/release",
                json={"outcome": outcome, "push": push},
            )
        )
        assert response.status_code == 200, response.text

    @rule()
    def the_psp_sends_everything_again(self) -> None:
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
        )
        assert response.status_code == 200

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        self.run(self._catch_up())

    @rule(data=st.data(), with_call=st.booleans())
    def crash_between_commits(self, data: st.DataObject, with_call: bool) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None or consumer_kind(candidate.consumer) != "effect":
            return

        async def step() -> None:
            crashing = await connect(TEST_URL, autocommit=True)
            try:
                assert await try_lock_aggregate(crashing, candidate)
                async with crashing.transaction():
                    prepared = await prepare_delivery(crashing, candidate, self.now)
                if with_call and isinstance(prepared, Prepared):
                    await consumers.call(self.http, ENV, prepared.call)
            finally:
                await crash(crashing)

        self.run(step())

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(operation=st.sampled_from(OPERATIONS), fault=st.sampled_from(FAULTS))
    def script_a_fault(self, operation: str, fault: str) -> None:
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/faults",
                json={"account": "acct", "operation": operation, "fault": fault},
            )
        )
        assert response.status_code == 204

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1

    @rule(seconds=st.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @rule()
    def clock_passes_a_quotes_hold(self) -> None:
        self.now += QUOTE_VALID_FOR

    @rule()
    def clock_passes_a_qr_codes_life(self) -> None:
        self.now += QR_LIFETIME

    @precondition(lambda self: self.now < T0 + SESSION_LIFETIME)
    @rule()
    def clock_passes_the_sessions_life(self) -> None:
        self.now = T0 + SESSION_LIFETIME

    # --- checks after every action ---------------------------------------

    @invariant()
    def each_session_matches_its_attempts(self) -> None:
        cur = self.run(
            self.worker.execute(
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
        )
        rows = self.run(cur.fetchall())
        for session_id, state, open_count, person, orders, ending in rows:
            match state:
                case "paying":
                    # Or its attempt has ended and the session's own step,
                    # back to quoted or on to its order, is still to run.
                    assert open_count == 1 or person > 0 or ending > 0, session_id
                    assert orders == 0
                case "completed":
                    assert orders == 1 and open_count == 0, session_id
                case "quoted" | "quoting" | "refused" | "expired" | "open":
                    assert open_count == 0 and orders == 0, (session_id, state)
                case _:
                    raise AssertionError(state)

    @invariant()
    def each_order_has_its_capture_entry_and_clearing_matches(self) -> None:
        cur = self.run(
            self.worker.execute(
                """
                SELECT count(*) FROM order_ o
                WHERE NOT EXISTS (SELECT 1 FROM journal_entry e
                  WHERE e.idempotency_key = 'capture:' || o.attempt_id::text)
                """
            )
        )
        assert self.run(cur.fetchone()) == (0,)
        cur = self.run(
            self.worker.execute(
                """
                SELECT
                  (SELECT coalesce(sum(amount_minor), 0) FROM journal_line
                    WHERE account_kind = 'psp_clearing'),
                  (SELECT coalesce(sum(a.amount_minor), 0) FROM order_ o
                    JOIN payment_attempt a ON a.id = o.attempt_id)
                """
            )
        )
        booked, ordered = self.run(cur.fetchone()) or (None, None)
        assert booked == ordered

    @invariant()
    def the_psp_charged_nothing_twice_and_nothing_unseen(self) -> None:
        cur = self.run(
            self.worker.execute(
                """
                SELECT c.reference, a.state::text, c.amount_minor, a.amount_minor
                FROM fake_vendor.psp_fake_charge c
                LEFT JOIN payment_attempt a ON a.id::text = c.reference
                """
            )
        )
        references: set[str] = set()
        for reference, state, charged, amount in self.run(cur.fetchall()):
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

    @invariant()
    def the_psp_made_at_most_one_payment_per_attempt(self) -> None:
        """A keyed account replays a resent create; on an unkeyed one we
        never resend. Either way one payment per reference, so a buyer can
        never be shown, or pay, a second one."""
        cur = self.run(
            self.worker.execute(
                "SELECT reference FROM fake_vendor.psp_fake_payment"
                " GROUP BY reference HAVING count(*) > 1"
            )
        )
        assert self.run(cur.fetchall()) == []

    @invariant()
    def no_advisory_lock_is_left_held(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND database = (SELECT oid FROM pg_database"
                " WHERE datname = current_database())"
            )
        )
        assert self.run(cur.fetchone()) == (0,)

    # --- helpers ---------------------------------------------------------

    async def _faults_waiting(self) -> int:
        cur = await self.worker.execute(
            "SELECT count(*) FROM fake_vendor.psp_fake_fault_next"
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _catch_up(self) -> None:
        for _ in range(100):
            candidates = await find_candidates(self.worker, self.now, 50)
            if not candidates:
                return
            for candidate in candidates:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        raise AssertionError("the worker never caught up")

    def _draw_candidate(self, data: st.DataObject) -> Candidate | None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return None
        return data.draw(st.sampled_from(candidates))

    async def _drain_and_check(self) -> None:
        await self.worker.execute("DELETE FROM fake_vendor.psp_fake_fault_next")
        self.network.refuse_next = 0
        # Webhooks the PSP could not deliver are sent again, as it would.
        await self.control.post(
            "http://mor/_fake/psp/acct/events/redeliver", json={"all": False}
        )
        for _ in range(20):
            self.now += timedelta(minutes=5)
            await self._catch_up()
        cur = await self.worker.execute(
            "SELECT id, state::text FROM payment_attempt"
            " WHERE state IN ('requested', 'authorised', 'cancelling')"
        )
        stuck = await cur.fetchall()
        assert stuck == [], f"attempts stuck after the drain: {stuck}"
        cur = await self.worker.execute(
            """
            SELECT c.reference FROM fake_vendor.psp_fake_charge c
            JOIN payment_attempt a ON a.id::text = c.reference
            WHERE a.state NOT IN ('captured', 'needs_person')
            """
        )
        unbooked = await cur.fetchall()
        assert unbooked == [], f"charges neither booked nor with a person: {unbooked}"


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_payments_survive_faults_crashes_double_submits_and_deadlines(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        PaymentFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
