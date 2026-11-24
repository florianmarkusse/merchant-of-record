"""Settlement (docs/plan-payments.md §7) as a Hypothesis state machine
against mor_test, the fake PSP and the worker: the stage after the capture,
in a machine of its own so it neither dilutes the order fuzzer's reach nor
waits behind it (docs/plan-architecture.md §10).

The run starts deep: one to three sessions of one shop, each paid by card
and approved at the PSP; for each the run draws whether our books have
caught up (the order made) or not, so settlement can get ahead of them.
The account's wiring and its settlement mode (report file or API) are
drawn; a first batch is cut (with faults, maybe), fetched whole and its
documents decoded in a drawn order (a page before its payout, an older
page before a newer one), and, as drawn, booked as each is decoded (a
payout reconciled against some of its lines; a line for a payment our
books have not caught up with waits) or left for the actions, with the
payments' own news.
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
import random
import uuid
from collections import Counter
from collections.abc import Coroutine
from dataclasses import dataclass
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
from psycopg.rows import class_row

from mor.db.connect import Connection, connect
from mor.db.fetch import one_row, one_value
from mor.fakes.psp import CUT_FAULTS, CutFault
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
from mor.psp.port import CaptureMode, SettlementMode, StatusMode
from mor.settlement.fetch import start_settlement_reading
from tests.flow import reach
from tests.flow.payment_checks import (
    Handled,
    catch_up,
    check_drained,
    check_every_action,
    check_settlement_drained,
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


@dataclass(frozen=True)
class _ArrivalOrders:
    line_first: bool
    older_page_first: bool
    open_while_arriving: bool


@dataclass(frozen=True, kw_only=True)
class _Reached:
    booked_lines: int
    payout_booked: bool
    balanced: bool
    net_off: bool
    counted: bool


T0 = NOW
READ_FAULTS = ("fail_500", "fail_500_after_work", "timeout_after_work")
SETTLEMENT_STEPS = (
    ConsumerName.SETTLEMENT_LINE_BOOK,
    ConsumerName.SETTLEMENT_PAYOUT_BOOK,
    ConsumerName.SETTLEMENT_PAYOUT_RECONCILE,
)

MILESTONES = (
    "a settled line booked",
    "a payout booked",
    "a line waited for its order",
    "several lines booked",
    "crash in settlement_line_book",
    "crash in settlement_payout_book",
    "crash in settlement_payout_reconcile",
    "crash in psp_inbox_decode",
    "crash around a settlement read",
    "met again: settlement_line_book",
    "met again: settlement_payout_book",
    "met again: settlement_payout_reconcile",
    "met again: psp_inbox_decode",
    "exception: unknown_reference",
    "exception: amount_differs",
    "exception: settled_twice",
    "exception: unknown_type",
    # The orders plan-payments.md §7 must take, seen while
    # they last, and what reconciling ends in.
    "a line stored before its payout",
    "an older page stored before a newer one",
    "a payout open while its lines arrive",
    "a payout balanced",
    "a payout open: its net is off",
    "a balance transfer counted",
)
TALLY: Counter[str] = Counter()


class SettlementFlow(RuleBasedStateMachine):
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
        status_mode=strategies.sampled_from(list(StatusMode)),
        capture_mode=strategies.sampled_from(list(CaptureMode)),
        guard=strategies.sampled_from(
            (DuplicateGuard.VENDOR_CONSTRAINT, DuplicateGuard.NONE)
        ),
        settlement_mode=strategies.sampled_from(list(SettlementMode)),
        booked=strategies.lists(strategies.booleans(), min_size=1, max_size=3),
        first_cut=strategies.lists(
            strategies.sampled_from(CUT_FAULTS), max_size=2, unique=True
        ),
        book_first_cut=strategies.booleans(),
        decode_order=strategies.randoms(use_true_random=False),
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
        decode_order: random.Random,
    ) -> None:
        """A session per entry of `booked`, each approved at the PSP, and
        caught up in our books where its entry says so; then a first cut,
        fetched whole and decoded in a drawn order, and booked as it is
        decoded if drawn so: the orders a payout and its pages arrive in
        come from the start, not from luck in the actions
        (plan-payments.md §7)."""
        self.guard = guard

        async def step() -> None:
            shop = await make_shop(
                TEST_URL,
                self.control,
                Wiring(
                    status_mode=status_mode,
                    capture_mode=capture_mode,
                    guard=guard,
                    settlement_mode=settlement_mode,
                ),
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
                # The payment waits on the buyer.
                waiting_id = await one_value(
                    self.worker,
                    "SELECT id FROM fake_vendor.psp_fake_payment"
                    " WHERE reference = %s AND status = 'requires_action'",
                    (str(paid.attempt_id),),
                    str,
                )
                response = await self.control.post(
                    f"http://mor/_fake/psp/acct/payments/{waiting_id}/release",
                    json={"outcome": "paid", "push": "once"},
                )
                assert response.status_code == 200, response.text
                if caught_up:
                    await self._books_catch_up()
                self.sessions.append(session_id)
            await self._cut(first_cut)
            self.now += self.settlement_interval
            await self._fetch_settlement()
            await self._decode_in_order(decode_order, book_first_cut)

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
        if candidate.consumer == ConsumerName.PSP_SETTLEMENT_FETCH:
            self.met.add("crash around a settlement read")
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @precondition(lambda self: self.run(self._settlement_steps_due()) != [])
    @rule(data=strategies.data())
    def crash_inside_a_settlement_step(self, data: strategies.DataObject) -> None:
        """Only drawn while one is due: booking a line or a payout is a
        short moment among many deliveries."""
        candidate = data.draw(
            strategies.sampled_from(self.run(self._settlement_steps_due()))
        )
        self.met.add(f"crash in {candidate.consumer.value}")
        self.run(crash_in_a_local_step(candidate, self.now))

    @precondition(lambda self: self.run(self._settlement_steps_handled()) != [])
    @rule(data=strategies.data())
    def a_settlement_step_meets_its_fact_again(
        self, data: strategies.DataObject
    ) -> None:
        handled = self.run(self._settlement_steps_handled())
        chosen = data.draw(strategies.sampled_from(handled))
        self.met.add(f"met again: {chosen.consumer}")
        self.run(meet_again(chosen, self.now))

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
        response = self.run(
            self.control.post(
                "http://mor/_fake/psp/acct/events/redeliver", json={"all": True}
            )
        )
        assert response.status_code == 200

    @rule(seconds=strategies.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @rule()
    def clock_passes_the_settlement_interval(self) -> None:
        """The account's next look for settlement comes due."""
        self.now += self.settlement_interval

    @rule(
        faults=strategies.lists(
            strategies.sampled_from(CUT_FAULTS), max_size=2, unique=True
        )
    )
    def the_psp_cuts_a_settlement(self, faults: list[CutFault]) -> None:
        self.run(self._cut(faults))

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(fault=strategies.sampled_from(READ_FAULTS))
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

    @invariant()
    def note_arrival_orders(self) -> None:
        """Not a rule: the orders lines and payouts arrive in, seen while
        they last."""
        self.met |= self.run(self._arrival_orders())

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

    async def _books_catch_up(self) -> None:
        """Our side of the payment, through to its order and invoice: what
        is due now. A polled account's look is due later, so the clock
        passes its interval first."""
        self.now += timedelta(minutes=1)
        await catch_up(self.worker, self.http, self.now)

    async def _fetch_settlement(self) -> None:
        """Only the looks, until none is due: every document stored, none
        decoded."""
        for _ in range(50):
            ran = False
            for candidate in await find_candidates(self.worker, self.now, 50):
                if candidate.consumer == ConsumerName.PSP_SETTLEMENT_FETCH:
                    result = await run_one(
                        self.worker, self.http, ENV, candidate, self.now
                    )
                    assert not isinstance(result, Failed), result
                    ran = True
            if not ran:
                return
        raise AssertionError("the reading never finished")

    async def _decode_in_order(self, order: random.Random, book: bool) -> None:
        """Each stored document decoded in the drawn order; with `book`, the
        settlement steps it leads to run before the next is decoded, so a
        payout is reconciled against some of its lines."""
        for _ in range(50):
            decodes: list[Candidate] = []
            for candidate in await find_candidates(self.worker, self.now, 50):
                if candidate.consumer == ConsumerName.PSP_INBOX_DECODE:
                    decodes.append(candidate)
            if not decodes:
                return
            chosen = order.choice(decodes)
            result = await run_one(self.worker, self.http, ENV, chosen, self.now)
            assert not isinstance(result, Failed), result
            if book:
                await self._book_settlement()
            self.met |= await self._arrival_orders()
        raise AssertionError("the decoding never finished")

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
        return await one_value(
            self.worker,
            "SELECT count(*) FROM fake_vendor.psp_fake_fault_next",
            None,
            int,
        )

    async def _lines_waiting(self) -> bool:
        cur = await self.worker.execute(
            "SELECT 1 FROM settlement_line WHERE state = 'waiting' LIMIT 1"
        )
        return await cur.fetchone() is not None

    async def _arrival_orders(self) -> set[str]:
        seen: set[str] = set()
        cur = await self.worker.cursor(row_factory=class_row(_ArrivalOrders)).execute(
            """
            SELECT
              EXISTS (SELECT 1 FROM settlement_line l WHERE NOT EXISTS (
                SELECT 1 FROM settlement_payout p
                WHERE p.account_id = l.account_id
                  AND p.vendor_payout_id = l.vendor_payout_id)) AS line_first,
              -- Pages come newest first: an older line stored first came on
              -- a later page decoded before an earlier one.
              EXISTS (SELECT 1
                FROM settlement_line older
                JOIN fake_vendor.psp_fake_settlement_line fo
                  ON fo.id = older.vendor_line_id
                JOIN settlement_line newer
                  ON newer.account_id = older.account_id
                 AND newer.vendor_payout_id = older.vendor_payout_id
                JOIN fake_vendor.psp_fake_settlement_line fn
                  ON fn.id = newer.vendor_line_id
                JOIN psp_account a ON a.id = older.account_id
                WHERE a.settlement_mode = 'api'
                  AND fo.seq < fn.seq AND older.id < newer.id) AS older_page_first,
              EXISTS (SELECT 1 FROM settlement_payout p
                JOIN fake_vendor.psp_fake_settlement_batch f
                  ON f.id = p.vendor_payout_id
                WHERE p.reconciliation = 'open' AND f.fault IS NULL
                  AND (SELECT count(*) FROM settlement_line l
                       WHERE l.account_id = p.account_id
                         AND l.vendor_payout_id = p.vendor_payout_id)
                    < (SELECT count(*) FROM fake_vendor.psp_fake_settlement_line fl
                       WHERE fl.batch_id = f.id)) AS open_while_arriving
            """
        )
        orders = await one_row(cur, "the arrival orders")
        if orders.line_first:
            seen.add("a line stored before its payout")
        if orders.older_page_first:
            seen.add("an older page stored before a newer one")
        if orders.open_while_arriving:
            seen.add("a payout open while its lines arrive")
        return seen

    async def _reached(self) -> set[str]:
        reached = set(self.met & set(MILESTONES))
        cur = await self.worker.cursor(row_factory=class_row(_Reached)).execute(
            """
            SELECT
              (SELECT count(*) FROM settlement_line WHERE state = 'booked')
                AS booked_lines,
              EXISTS (SELECT 1 FROM settlement_payout WHERE booked_at IS NOT NULL)
                AS payout_booked,
              EXISTS (SELECT 1 FROM settlement_payout
                      WHERE reconciliation = 'balanced') AS balanced,
              EXISTS (SELECT 1 FROM settlement_payout p
                      JOIN fake_vendor.psp_fake_settlement_batch f
                        ON f.id = p.vendor_payout_id
                      WHERE p.reconciliation = 'open' AND f.fault = 'net_differs')
                AS net_off,
              EXISTS (SELECT 1 FROM settlement_line WHERE state = 'counted')
                AS counted
            """
        )
        found = await one_row(cur, "the settlement milestones")
        booked_lines = found.booked_lines
        payout_booked = found.payout_booked
        balanced = found.balanced
        net_off = found.net_off
        counted = found.counted
        if booked_lines >= 1:
            reached.add("a settled line booked")
        if booked_lines >= 2:
            reached.add("several lines booked")
        if payout_booked:
            reached.add("a payout booked")
        if balanced:
            reached.add("a payout balanced")
        if net_off:
            reached.add("a payout open: its net is off")
        if counted:
            reached.add("a balance transfer counted")
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
