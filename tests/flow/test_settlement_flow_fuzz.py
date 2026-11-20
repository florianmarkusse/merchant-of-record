"""Settlement (docs/plan-payments.md §7.1) as a Hypothesis state machine
against mor_test, the fake PSP and the worker: the stage after the capture,
in a machine of its own so it neither dilutes the order fuzzer's reach nor
waits behind it (docs/plan-fuzz-reach.md §3).

The run starts deep: one to three sessions of one shop, each paid by card
and approved at the PSP; for each the run draws whether our books have
caught up (the order made) or not, so settlement can get ahead of them.
The account's wiring and its settlement mode (report file or API) are
drawn; a first batch is cut (with faults, maybe) and read, and, as drawn,
booked as far as it goes (a line for a payment our books have not caught
up with then waits) or left for the actions, with the payments' own news.
Actions: run one delivery; the worker catches up; crash inside a local
step before its commit (a settlement step's above all, while one is due),
or around an effect's call (a settlement read among them); a handled fact
met again by its consumer (a settlement step's above all); the PSP sends every
event again; the clock moves, or passes the settlement interval; the PSP
cuts a settlement, some batches with faults; a settlement read fails.

After every action, the rules in payment_checks.py, settlement's among
them. After draining with faults off and one last clean cut: every
payment completed (or, with no duplicate guard, with a person), and
everything the fake settled booked once, or the exception its fault calls
for, with `psp_clearing` holding exactly what the fake has not paid out.
"""

import asyncio
import os
import uuid
from collections import Counter
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

from mor.db.connect import Connection, connect
from mor.fakes.psp import CUT_FAULTS, CutFault
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName, consumer_kind
from mor.messaging.worker import Candidate, Failed, find_candidates, run_one
from mor.payments.flow import Paying, pay
from mor.psp.port import CaptureMode, SettlementMode, StatusMode
from mor.settlement.fetch import start_settlement_reading
from tests.flow import reach
from tests.flow.payment_checks import (
    Handled,
    catch_up,
    check_drained,
    check_every_action,
    check_settlement_drained,
    crash_around_a_call,
    crash_in_a_local_step,
    handled_locally,
    meet_again,
)
from tests.helpers import ENV, TEST_URL, empty_tables, wire
from tests.payments.support import (
    ACTOR,
    NOW,
    SGD_PRICE,
    Wiring,
    make_shop,
    quoted_session,
)


T0 = NOW
READ_FAULTS = ("fail_500", "fail_500_after_work", "timeout_after_work")
SETTLEMENT_STEPS = (
    ConsumerName.SETTLEMENT_LINE_BOOK,
    ConsumerName.SETTLEMENT_PAYOUT_BOOK,
)

MILESTONES = (
    "a settled line booked",
    "a payout booked",
    "a line waited for its order",
    "several lines booked",
    "crash in settlement_line_book",
    "crash in settlement_payout_book",
    "crash in psp_inbox_decode",
    "crash around a settlement read",
    "met again: settlement_line_book",
    "met again: settlement_payout_book",
    "met again: psp_inbox_decode",
    "exception: unknown_reference",
    "exception: amount_differs",
    "exception: settled_twice",
    "exception: unknown_type",
    "exception: net_differs",
)
TALLY: Counter[str] = Counter()


class SettlementFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        self.now = T0
        wired = wire(TEST_URL, lambda: self.now)
        self.http = wired.http
        self.control = wired.control
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.sessions: list[uuid.UUID] = []
        self.guard = DuplicateGuard.VENDOR_CONSTRAINT
        self.settlement_interval = timedelta(hours=1)
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
        status_mode=st.sampled_from(list(StatusMode)),
        capture_mode=st.sampled_from(list(CaptureMode)),
        guard=st.sampled_from((DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)),
        settlement_mode=st.sampled_from(list(SettlementMode)),
        booked=st.lists(st.booleans(), min_size=1, max_size=3),
        first_cut=st.lists(st.sampled_from(CUT_FAULTS), max_size=2, unique=True),
        book_first_cut=st.booleans(),
    )
    def approved_payments(
        self,
        status_mode: StatusMode,
        capture_mode: CaptureMode,
        guard: DuplicateGuard,
        settlement_mode: SettlementMode,
        booked: list[bool],
        first_cut: list[CutFault],
        book_first_cut: bool,
    ) -> None:
        """A session per entry of `booked`, each approved at the PSP, and
        caught up in our books where its entry says so; then a first cut,
        read and stored, and booked if drawn so."""
        self.guard = guard

        async def step() -> None:
            shop = await make_shop(
                TEST_URL,
                self.control,
                Wiring(status_mode, capture_mode, guard, settlement_mode),
            )
            self.settlement_interval = shop.account.settlement_interval
            async with await connect(TEST_URL) as conn:
                await start_settlement_reading(conn, shop.account.id, ACTOR, self.now)
                await conn.commit()
            for caught_up in booked:
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
                if caught_up:
                    await self._books_catch_up()
                self.sessions.append(session_id)
            await self._cut(first_cut)
            self.now += self.settlement_interval
            await self._read_settlement()
            if book_first_cut:
                await self._book_settlement()

        self.run(step())

    # --- actions ---------------------------------------------------------

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidate = self._draw(data, None)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        self.run(catch_up(self.worker, self.http, self.now))

    @rule(data=st.data())
    def crash_inside_a_local_step(self, data: st.DataObject) -> None:
        candidate = self._draw(data, "local")
        if candidate is None:
            return
        self.met.add(f"crash in {candidate.consumer.value}")
        self.run(crash_in_a_local_step(candidate, self.now))

    @rule(data=st.data(), with_call=st.booleans())
    def crash_around_an_effects_call(
        self, data: st.DataObject, with_call: bool
    ) -> None:
        candidate = self._draw(data, "effect")
        if candidate is None:
            return
        if candidate.consumer == ConsumerName.PSP_SETTLEMENT_FETCH:
            self.met.add("crash around a settlement read")
        self.run(crash_around_a_call(candidate, with_call, self.http, self.now))

    @precondition(lambda self: self.run(self._settlement_steps_due()) != [])
    @rule(data=st.data())
    def crash_inside_a_settlement_step(self, data: st.DataObject) -> None:
        """Only drawn while one is due: booking a line or a payout is a
        short moment among many deliveries."""
        candidate = data.draw(st.sampled_from(self.run(self._settlement_steps_due())))
        self.met.add(f"crash in {candidate.consumer.value}")
        self.run(crash_in_a_local_step(candidate, self.now))

    @precondition(lambda self: self.run(self._settlement_steps_handled()) != [])
    @rule(data=st.data())
    def a_settlement_step_meets_its_fact_again(self, data: st.DataObject) -> None:
        handled = self.run(self._settlement_steps_handled())
        chosen = data.draw(st.sampled_from(handled))
        self.met.add(f"met again: {chosen.consumer}")
        self.run(meet_again(chosen, self.now))

    @rule(data=st.data())
    def a_consumer_meets_its_fact_again(self, data: st.DataObject) -> None:
        """See payment_checks.meet_again."""
        handled = self.run(handled_locally(self.worker))
        if not handled:
            return
        chosen = data.draw(st.sampled_from(handled))
        self.met.add(f"met again: {chosen.consumer}")
        self.run(meet_again(chosen, self.now))

    @rule()
    def the_psp_sends_everything_again(self) -> None:
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
        )
        assert response.status_code == 200

    @rule(seconds=st.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @rule()
    def clock_passes_the_settlement_interval(self) -> None:
        """The account's next look for settlement comes due."""
        self.now += self.settlement_interval

    @rule(faults=st.lists(st.sampled_from(CUT_FAULTS), max_size=2, unique=True))
    def the_psp_cuts_a_settlement(self, faults: list[CutFault]) -> None:
        self.run(self._cut(faults))

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(fault=st.sampled_from(READ_FAULTS))
    def a_settlement_read_fails(self, fault: str) -> None:
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/faults",
                json={"account": "acct", "operation": "settlements", "fault": fault},
            )
        )
        assert response.status_code == 204, response.text

    # --- checks ----------------------------------------------------------

    @invariant()
    def the_payment_rules_hold(self) -> None:
        self.run(check_every_action(self.worker))

    @invariant()
    def note_lines_waiting(self) -> None:
        """Not a rule: a waiting line is a moment, seen only while it lasts."""
        if self.run(self._lines_waiting()):
            self.met.add("a line waited for its order")

    # --- helpers ---------------------------------------------------------

    def _draw(self, data: st.DataObject, kind: str | None) -> Candidate | None:
        found = self.run(find_candidates(self.worker, self.now, 50))
        if kind is not None:
            found = [c for c in found if consumer_kind(c.consumer) == kind]
        if not found:
            return None
        return data.draw(st.sampled_from(found))

    async def _books_catch_up(self) -> None:
        """Our side of the payment, through to its order and invoice: what
        is due now. A polled account's look is due later, so the clock
        passes its interval first."""
        self.now += timedelta(minutes=1)
        await catch_up(self.worker, self.http, self.now)

    async def _read_settlement(self) -> None:
        """Only the reading: looks and decoding, until nothing of them is
        due. Booking is left to the actions."""
        reading = (ConsumerName.PSP_SETTLEMENT_FETCH, ConsumerName.PSP_INBOX_DECODE)
        for _ in range(50):
            ran = False
            for candidate in await find_candidates(self.worker, self.now, 50):
                if candidate.consumer in reading:
                    result = await run_one(
                        self.worker, self.http, ENV, candidate, self.now
                    )
                    assert not isinstance(result, Failed), result
                    ran = True
            if not ran:
                return
        raise AssertionError("the reading never finished")

    async def _book_settlement(self) -> None:
        """The settlement steps due now, once each: nothing else runs, so a
        line whose payment is not in our books yet waits."""
        for candidate in await self._settlement_steps_due():
            result = await run_one(self.worker, self.http, ENV, candidate, self.now)
            assert not isinstance(result, Failed), result
        if await self._lines_waiting():
            self.met.add("a line waited for its order")

    async def _settlement_steps_due(self) -> list[Candidate]:
        found: list[Candidate] = []
        for candidate in await find_candidates(self.worker, self.now, 50):
            if candidate.consumer in SETTLEMENT_STEPS:
                found.append(candidate)
        return found

    async def _settlement_steps_handled(self) -> list[Handled]:
        found: list[Handled] = []
        for handled in await handled_locally(self.worker):
            if ConsumerName(handled.consumer) in SETTLEMENT_STEPS:
                found.append(handled)
        return found

    async def _cut(self, faults: list[CutFault]) -> None:
        response = await self.control.post(
            "http://mor/_fake/psp/acct/settlements", json={"faults": faults}
        )
        assert response.status_code == 200, response.text

    async def _faults_waiting(self) -> int:
        cur = await self.worker.execute(
            "SELECT count(*) FROM fake_vendor.psp_fake_fault_next"
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _lines_waiting(self) -> bool:
        cur = await self.worker.execute(
            "SELECT 1 FROM settlement_line WHERE state = 'waiting' LIMIT 1"
        )
        return await cur.fetchone() is not None

    async def _reached(self) -> set[str]:
        reached = set(self.met & set(MILESTONES))
        cur = await self.worker.execute(
            """
            SELECT
              (SELECT count(*) FROM settlement_line WHERE state = 'booked'),
              EXISTS (SELECT 1 FROM settlement_batch WHERE booked_at IS NOT NULL)
            """
        )
        row = await cur.fetchone()
        assert row is not None
        if row[0] >= 1:
            reached.add("a settled line booked")
        if row[0] >= 2:
            reached.add("several lines booked")
        if row[1]:
            reached.add("a payout booked")
        cur = await self.worker.execute(
            "SELECT DISTINCT kind::text FROM settlement_exception"
        )
        for (kind,) in await cur.fetchall():
            reached.add(f"exception: {kind}")
        return reached & set(MILESTONES)

    async def _drain_and_check(self) -> None:
        # Faults off: a read scripted to fail and not yet tried goes.
        await self.worker.execute("DELETE FROM fake_vendor.psp_fake_fault_next")
        await self.control.post(
            "http://mor/_fake/psp/acct/events/redeliver", json={"all": False}
        )
        for _ in range(20):
            self.now += timedelta(minutes=5)
            await catch_up(self.worker, self.http, self.now)
        # One last cut with no faults, so every run settles what it took.
        await self._cut([])
        for _ in range(3):
            self.now += self.settlement_interval
            await catch_up(self.worker, self.http, self.now)
        await check_every_action(self.worker)
        await check_drained(self.worker)
        await check_settlement_drained(self.worker)
        cur = await self.worker.execute(
            "SELECT state::text, count(*) FROM checkout_session GROUP BY state"
        )
        states = dict(await cur.fetchall())
        assert sum(states.values()) == len(self.sessions)
        if self.guard == DuplicateGuard.VENDOR_CONSTRAINT:
            assert states == {"completed": len(self.sessions)}, states


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_settlement_books_each_line_once_through_crashes_and_faults(
    migrated_test_database: str,
) -> None:
    TALLY.clear()
    run_state_machine_as_test(
        SettlementFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
    reach.finish("settlement-flow", MILESTONES, TALLY)
