"""The payment flow up to the capture as a Hypothesis state machine
against mor_test, the fake PSP and the worker (docs/plan-payments.md §8,
docs/plan-architecture.md §10). Each run draws the account's wiring: how news
arrives (fat or thin webhooks, polling), how it captures (sale, or
authorise then capture) and its duplicate guard. What follows the capture
is fuzzed in depth by the order machine (test_order_flow_fuzz.py); this
one drains through it and checks it.

Setup draws the wiring and up to two PSP faults planned for the whole run.
Actions: the buyer pays (also twice at once), pays and answers at once
(approve, decline, let it expire, choose another way, leave), pays another
way, answers at the PSP; the PSP pushes again every event, newest first;
run a delivery; the worker catches up; crash after an effect's `prepare`,
or after its call, or inside a local step before its commit; script a PSP
fault; refuse the next connection; move the clock, now and then past a
QR's life, and once a payment was tried past a quote's hold or a
session's life.

After every action, the rules in payment_checks.py: a session's state
matches its attempts; each order has its one capture entry and
`psp_clearing` holds exactly the captured amounts; the PSP charged no
reference twice and nothing we did not see; at most one PSP payment per
attempt; invoice numbers without a gap, each for what its payment
captured; no advisory lock left held. After draining with faults off: no
attempt stuck, every charge booked or with a person, every order invoiced.
Each run's reach is recorded (reach.py).
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

from mor.checkout.flow import QUOTE_VALID_FOR, refresh_quote
from mor.checkout.records import SESSION_LIFETIME
from mor.db.connect import Connection, connect
from mor.db.fetch import one_value
from mor.fakes.psp import QR_LIFETIME
from mor.messaging.delivery import (
    Candidate,
)
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import Effect, Local, consumer_kind
from mor.messaging.worker import (
    Failed,
    find_candidates,
    run_one,
)
from mor.payments.flow import Paying, pay, pay_another_way
from mor.psp.port import CaptureMode, StatusMode
from tests.flow import reach
from tests.flow.payment_checks import (
    catch_up,
    check_drained,
    check_every_action,
    crash_in_a_local_step,
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
FAULTS = ("fail_500", "fail_500_after_work", "timeout_after_work", "decline")
OPERATIONS = ("create", "capture", "cancel", "get")

# The fact types a run should reach, read from its outbox before the final
# drain. Several orders at once are the order machine's.
MILESTONES = (
    "payment_attempt_created",
    "payment_action_required",
    "payment_captured",
    "payment_declined",
    "payment_expired",
    "payment_cancelled",
    "payment_needs_person",
    "order_paid",
    "invoice_issued",
    "checkout_session_expired",
)
TALLY: Counter[str] = Counter()


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
        # The clock's jumps past a quote's hold or a session's life wait
        # for a first attempt: early, they leave nothing to pay for the
        # rest of the run (docs/plan-architecture.md §10).
        self.attempted = False

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

    # --- actions ---------------------------------------------------------

    @initialize(
        status_mode=strategies.sampled_from(list(StatusMode)),
        capture_mode=strategies.sampled_from(list(CaptureMode)),
        guard=strategies.sampled_from(
            (DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)
        ),
        planned=strategies.lists(
            strategies.tuples(
                strategies.sampled_from(OPERATIONS), strategies.sampled_from(FAULTS)
            ),
            max_size=2,
        ),
    )
    def a_shop_with_two_quoted_sessions(
        self,
        status_mode: StatusMode,
        capture_mode: CaptureMode,
        guard: DuplicateGuard,
        planned: list[tuple[str, str]],
    ) -> None:
        """`planned`: faults decided for the whole run, each waiting at the
        PSP for its operation's first call (as FoundationDB's BUGGIFY sites
        are on or off per run, docs/plan-architecture.md §10). A fault on the
        one call that matters (a capture on an unkeyed account) is then
        common, where scripting it by a later action is rare."""

        async def step() -> None:
            shop = await make_shop(
                TEST_URL,
                self.control,
                Wiring(status_mode=status_mode, capture_mode=capture_mode, guard=guard),
            )
            for _ in range(2):
                session_id, option = await quoted_session(
                    TEST_URL, shop.merchant_id, SGD_PRICE, self.now
                )
                self.sessions.append(session_id)
                self.options[session_id] = option
            for operation, fault in planned:
                await self._script_fault(operation, fault)

        self.run(step())

    @rule(
        session=strategies.integers(0, 1),
        method=strategies.sampled_from(("card", "qr")),
        twice=strategies.booleans(),
    )
    def the_buyer_pays(self, session: int, method: str, twice: bool) -> None:
        session_id = self.sessions[session]

        async def one() -> None:
            async with await connect(TEST_URL) as conn:
                paid = await pay(
                    conn,
                    session_id,
                    self.options[session_id],
                    method,
                    "http://mor/c/back",
                    ACTOR,
                    self.now,
                )
                await conn.commit()
            if isinstance(paid, Paying):
                self.attempted = True

        async def step() -> None:
            if twice:
                await asyncio.gather(one(), one())
            else:
                await one()

        self.run(step())

    @rule(
        session=strategies.integers(0, 1),
        method=strategies.sampled_from(("card", "qr")),
        answer=strategies.sampled_from(
            ("paid", "paid", "declined", "expired", "leave", "another_way")
        ),
    )
    def the_buyer_pays_and_answers(
        self, session: int, method: str, answer: str
    ) -> None:
        """The way most buyers go, as one action, so runs reach the PSP's
        answers: pay, the worker makes the payment, the buyer answers it,
        chooses another way to pay, or leaves. What follows (the capture or
        the end, the order) is left to the other actions, crashes among
        them."""
        session_id = self.sessions[session]

        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                paid = await pay(
                    conn,
                    session_id,
                    self.options[session_id],
                    method,
                    "http://mor/c/back",
                    ACTOR,
                    self.now,
                )
                await conn.commit()
            if not isinstance(paid, Paying):
                return
            self.attempted = True
            if answer == "leave":
                return
            await catch_up(self.worker, self.http, self.now)
            if answer == "another_way":
                async with await connect(TEST_URL) as conn:
                    await pay_another_way(conn, session_id, ACTOR, self.now)
                    await conn.commit()
                return
            cur = await self.worker.execute(
                "SELECT id FROM fake_vendor.psp_fake_payment"
                " WHERE reference = %s AND status = 'requires_action'",
                (str(paid.attempt_id),),
            )
            row = await cur.fetchone()
            if row is None:  # a fault ended it, or it is still being made
                return
            response = await self.control.post(
                f"http://mor/_fake/psp/acct/payments/{row[0]}/release",
                json={"outcome": answer, "push": "once"},
            )
            assert response.status_code == 200, response.text

        self.run(step())

    @rule(session=strategies.integers(0, 1))
    def the_buyer_pays_another_way(self, session: int) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await pay_another_way(conn, self.sessions[session], ACTOR, self.now)
                await conn.commit()

        self.run(step())

    @rule(session=strategies.integers(0, 1))
    def the_buyer_asks_for_the_current_price(self, session: int) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await refresh_quote(conn, self.sessions[session], ENV, self.now)
                await conn.commit()

        self.run(step())

    @rule(
        data=strategies.data(),
        outcome=strategies.sampled_from(("paid", "paid", "declined", "expired")),
        push=strategies.sampled_from(("once", "twice", "bad_signature")),
    )
    def the_buyer_answers_at_the_psp(
        self, data: strategies.DataObject, outcome: str, push: str
    ) -> None:
        # Ordered by our attempt id (uuid7, in creation order), not the
        # fake's random `pay_…` id: a replay of the same example must draw
        # the same payment, or Hypothesis reports FlakyStrategyDefinition.
        cur = self.run(
            self.worker.execute(
                "SELECT id FROM fake_vendor.psp_fake_payment"
                " WHERE status = 'requires_action' ORDER BY reference"
            )
        )
        waiting = [row[0] for row in self.run(cur.fetchall())]
        if not waiting:
            return
        payment_id = data.draw(strategies.sampled_from(waiting))
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

    @rule(data=strategies.data())
    def run_a_delivery(self, data: strategies.DataObject) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        self.run(catch_up(self.worker, self.http, self.now))

    @rule(data=strategies.data(), with_call=strategies.booleans())
    def crash_between_commits(
        self, data: strategies.DataObject, with_call: bool
    ) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None or not isinstance(
            consumer_kind(candidate.consumer), Effect
        ):
            return
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @rule(data=strategies.data())
    def crash_inside_a_local_step(self, data: strategies.DataObject) -> None:
        """Drawn among local steps only: a local step is one transaction,
        and the window before it runs is short."""
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        local: list[Candidate] = []
        for found in candidates:
            if isinstance(consumer_kind(found.consumer), Local):
                local.append(found)
        if not local:
            return
        self.run(
            crash_in_a_local_step(data.draw(strategies.sampled_from(local)), self.now)
        )

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(
        operation=strategies.sampled_from(OPERATIONS),
        fault=strategies.sampled_from(FAULTS),
    )
    def script_a_fault(self, operation: str, fault: str) -> None:
        self.run(self._script_fault(operation, fault))

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1

    @rule(seconds=strategies.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @precondition(lambda self: self.attempted)
    @rule()
    def clock_passes_a_quotes_hold(self) -> None:
        self.now += QUOTE_VALID_FOR

    @rule()
    def clock_passes_a_qr_codes_life(self) -> None:
        self.now += QR_LIFETIME

    @precondition(lambda self: self.attempted and self.now < T0 + SESSION_LIFETIME)
    @rule()
    def clock_passes_the_sessions_life(self) -> None:
        self.now = T0 + SESSION_LIFETIME

    # --- checks after every action ---------------------------------------

    @invariant()
    def the_payment_rules_hold(self) -> None:
        self.run(check_every_action(self.worker))

    # --- helpers ---------------------------------------------------------

    async def _script_fault(self, operation: str, fault: str) -> None:
        response = await self.control.post(
            "http://mor/_fake/psp/faults",
            json={"account": "acct", "operation": operation, "fault": fault},
        )
        assert response.status_code == 204

    async def _faults_waiting(self) -> int:
        return await one_value(
            self.worker,
            "SELECT count(*) FROM fake_vendor.psp_fake_fault_next",
            None,
            int,
        )

    def _draw_candidate(self, data: strategies.DataObject) -> Candidate | None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return None
        return data.draw(strategies.sampled_from(candidates))

    async def _reached(self) -> set[str]:
        cur = await self.worker.execute(
            "SELECT DISTINCT message_type::text FROM outbox"
        )
        return {row[0] for row in await cur.fetchall()} & set(MILESTONES)

    async def _drain_and_check(self) -> None:
        await self.worker.execute("DELETE FROM fake_vendor.psp_fake_fault_next")
        self.network.refuse_next = 0
        # Webhooks the PSP could not deliver are sent again, as it would.
        await self.control.post(
            "http://mor/_fake/psp/acct/events/redeliver", json={"all": False}
        )
        for _ in range(20):
            self.now += timedelta(minutes=5)
            await catch_up(self.worker, self.http, self.now)
        await check_drained(self.worker)


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_payments_survive_faults_crashes_double_submits_and_deadlines(
    migrated_test_database: str,
) -> None:
    TALLY.clear()
    run_state_machine_as_test(
        PaymentFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
    reach.finish("payment-flow", MILESTONES, TALLY)
