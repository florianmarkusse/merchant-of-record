"""What follows a payment the buyer approved, as a Hypothesis state machine
against mor_test, the fake PSP and the worker (docs/plan-fuzz-reach.md §3):
the PSP's news decoded, the capture, the order with its ledger entry, the
invoice. Settlement, refunds and payouts join here as they are built.

The run starts deep: one to three sessions of one shop, each paid by card
and approved at the PSP, with everything after the approval still to run.
Its actions are the delivery machinery's own faults: run one delivery;
the worker catches up; crash inside a local step before its commit, or
around an effect's call (a capture, while one is due); a handled fact met
again by its consumer, past the inbox and committed, as a broker's
redelivery would; the PSP sends every event again; the clock moves, now
and then past the sessions' life.

After every action, the rules in payment_checks.py. After draining: the
same, and every session completed with one order and one invoice, numbered
1…n, or (only with no duplicate guard, after a crash around a call) its
attempt waiting on a person.
"""

import asyncio
import os
import uuid
from collections import Counter
from collections.abc import Coroutine
from datetime import timedelta

from hypothesis import settings, strategies
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from mor.checkout.records import SESSION_LIFETIME
from mor.db.connect import Connection, connect
from mor.messaging.delivery import (
    Candidate,
)
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName, Effect, Local, consumer_kind
from mor.messaging.worker import (
    Failed,
    find_candidates,
    run_one,
)
from mor.payments.flow import Paying, pay
from mor.psp.port import CaptureMode, StatusMode
from tests.flow import reach
from tests.flow.payment_checks import (
    catch_up,
    check_drained,
    check_every_action,
    crash_in_a_local_step,
    handled_locally,
    meet_again,
)
from tests.helpers import ENV, TEST_URL, crash_around_a_call, empty_tables, wire
from tests.payments.support import (
    ACTOR,
    NOW,
    SGD_PRICE,
    Wiring,
    make_shop,
    quoted_session,
)


T0 = NOW

# What a run should reach before its final drain: the facts in its outbox,
# and what its actions met on the way.
MILESTONES = (
    "payment_captured",
    "order_paid",
    "invoice_issued",
    "several orders",
    "crash in order_create",
    "crash in invoice_issue",
    "crash around a capture",
    "met again: order_create",
    "met again: invoice_issue",
    "events sent again after an order",
    "sessions' life passed",
)
TALLY: Counter[str] = Counter()


class OrderFlow(RuleBasedStateMachine):
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
        self.guard = DuplicateGuard.VENDOR_CONSTRAINT
        self.met: set[str] = set()

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            if self.sessions:
                reach.record(TALLY, self.run(self._reached()))
                self.run(self._drain_and_check())
        finally:
            self.run(self.http.aclose())
            self.run(self.control.aclose())
            self.run(self.worker.close())
            self.loop.close()

    # --- the deep start --------------------------------------------------

    @initialize(
        status_mode=strategies.sampled_from(list(StatusMode)),
        capture_mode=strategies.sampled_from(list(CaptureMode)),
        guard=strategies.sampled_from(
            (DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)
        ),
        count=strategies.integers(1, 3),
    )
    def approved_payments(
        self,
        status_mode: StatusMode,
        capture_mode: CaptureMode,
        guard: DuplicateGuard,
        count: int,
    ) -> None:
        """`count` sessions, each paid by card and approved at the PSP. The
        worker has made each payment; nothing after the approval has run."""
        self.guard = guard

        async def step() -> None:
            shop = await make_shop(
                TEST_URL,
                self.control,
                Wiring(status_mode=status_mode, capture_mode=capture_mode, guard=guard),
            )
            for _ in range(count):
                session_id, option = await quoted_session(
                    TEST_URL, shop.merchant_id, SGD_PRICE, self.now
                )
                async with await connect(TEST_URL) as conn:
                    paid = await pay(
                        conn,
                        session_id,
                        option,
                        "card",
                        "http://mor/c/back",
                        ACTOR,
                        self.now,
                    )
                    await conn.commit()
                assert isinstance(paid, Paying), paid
                await catch_up(self.worker, self.http, self.now)
                cur = await self.worker.execute(
                    "SELECT id FROM fake_vendor.psp_fake_payment"
                    " WHERE reference = %s AND status = 'requires_action'",
                    (str(paid.attempt_id),),
                )
                row = await cur.fetchone()
                assert row is not None, "the payment waits on the buyer"
                response = await self.control.post(
                    f"http://mor/_fake/psp/acct/payments/{row[0]}/release",
                    json={"outcome": "paid", "push": "once"},
                )
                assert response.status_code == 200, response.text
                self.sessions.append(session_id)

        self.run(step())

    # --- actions ---------------------------------------------------------

    @rule(data=strategies.data())
    def run_a_delivery(self, data: strategies.DataObject) -> None:
        candidate = self._draw(data, None)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        self.run(catch_up(self.worker, self.http, self.now))

    @rule(data=strategies.data())
    def crash_inside_a_local_step(self, data: strategies.DataObject) -> None:
        candidate = self._draw(data, Local)
        if candidate is None:
            return
        self.met.add(f"crash in {candidate.consumer.value}")
        self.run(crash_in_a_local_step(candidate, self.now))

    @rule(data=strategies.data(), with_call=strategies.booleans())
    def crash_around_an_effects_call(
        self, data: strategies.DataObject, with_call: bool
    ) -> None:
        candidate = self._draw(data, Effect)
        if candidate is None:
            return
        if candidate.consumer == ConsumerName.PSP_PAYMENT_CAPTURE:
            self.met.add("crash around a capture")
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @precondition(lambda self: self.run(self._captures_due()) != [])
    @rule(data=strategies.data(), with_call=strategies.booleans())
    def crash_around_a_capture(
        self, data: strategies.DataObject, with_call: bool
    ) -> None:
        """Only drawn while a capture is due: its moment is short, and the
        lost capture is the case the duplicate guard exists for."""
        candidate = data.draw(strategies.sampled_from(self.run(self._captures_due())))
        self.met.add("crash around a capture")
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @rule(data=strategies.data())
    def a_consumer_meets_its_fact_again(self, data: strategies.DataObject) -> None:
        """See payment_checks.meet_again."""
        handled = self.run(handled_locally(self.worker))
        if not handled:
            return
        chosen = data.draw(strategies.sampled_from(handled))
        self.met.add(f"met again: {chosen.consumer}")
        self.run(meet_again(chosen, self.now))

    @rule()
    def the_psp_sends_everything_again(self) -> None:
        if self.run(self._orders()) > 0:
            self.met.add("events sent again after an order")
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
        )
        assert response.status_code == 200

    @rule(seconds=strategies.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @precondition(lambda self: self.now < T0 + SESSION_LIFETIME)
    @rule()
    def clock_passes_the_sessions_life(self) -> None:
        self.met.add("sessions' life passed")
        self.now = T0 + SESSION_LIFETIME

    # --- checks ----------------------------------------------------------

    @invariant()
    def the_payment_rules_hold(self) -> None:
        self.run(check_every_action(self.worker))

    # --- helpers ---------------------------------------------------------

    def _draw(
        self, data: strategies.DataObject, kind: type[Local] | type[Effect] | None
    ) -> Candidate | None:
        found = self.run(find_candidates(self.worker, self.now, 50))
        if kind is not None:
            found = [c for c in found if isinstance(consumer_kind(c.consumer), kind)]
        if not found:
            return None
        return data.draw(strategies.sampled_from(found))

    async def _captures_due(self) -> list[Candidate]:
        found: list[Candidate] = []
        for candidate in await find_candidates(self.worker, self.now, 50):
            if candidate.consumer == ConsumerName.PSP_PAYMENT_CAPTURE:
                found.append(candidate)
        return found

    async def _orders(self) -> int:
        cur = await self.worker.execute("SELECT count(*) FROM order_")
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _reached(self) -> set[str]:
        cur = await self.worker.execute(
            "SELECT DISTINCT message_type::text FROM outbox"
        )
        reached = {row[0] for row in await cur.fetchall()} & set(MILESTONES)
        if await self._orders() >= 2:
            reached.add("several orders")
        return reached | (self.met & set(MILESTONES))

    async def _drain_and_check(self) -> None:
        await self.control.post(
            "http://mor/_fake/psp/acct/events/redeliver", json={"all": False}
        )
        for _ in range(20):
            self.now += timedelta(minutes=5)
            await catch_up(self.worker, self.http, self.now)
        await check_every_action(self.worker)
        await check_drained(self.worker)
        cur = await self.worker.execute(
            """
            SELECT s.state::text,
              (SELECT count(*) FROM order_ o WHERE o.session_id = s.id),
              (SELECT count(*) FROM order_ o JOIN invoice i ON i.order_id = o.id
                WHERE o.session_id = s.id),
              (SELECT string_agg(a.state::text, ',') FROM payment_attempt a
                WHERE a.session_id = s.id)
            FROM checkout_session s
            """
        )
        rows = await cur.fetchall()
        assert len(rows) == len(self.sessions)
        invoiced = 0
        for state, orders, invoices, attempts in rows:
            # Every approved payment ends in its order and invoice, or, on
            # an account with no duplicate guard whose call may or may not
            # have gone out, with a person (plan-architecture §5).
            if state == "completed":
                assert (orders, invoices, attempts) == (1, 1, "captured"), rows
                invoiced += 1
            else:
                assert (state, orders, attempts) == ("paying", 0, "needs_person")
                assert self.guard == DuplicateGuard.NONE, rows
        cur = await self.worker.execute("SELECT number FROM invoice ORDER BY number")
        numbers = [row[0] for row in await cur.fetchall()]
        assert numbers == list(range(1, invoiced + 1)), numbers


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_what_follows_an_approval_survives_crashes_and_redeliveries(
    migrated_test_database: str,
) -> None:
    TALLY.clear()
    run_state_machine_as_test(
        OrderFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
    reach.finish("order-flow", MILESTONES, TALLY)
