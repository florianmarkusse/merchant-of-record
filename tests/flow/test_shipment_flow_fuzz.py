"""Booking labels for paid orders and taking in what the carrier says
about them, as a Hypothesis state machine against mor_test, the fake
carrier and the worker (docs/plan-shipments.md §9, docs/plan-architecture.md
§10). Applying events and milestones join it in 6b steps 3 and 4.

The run starts deep: one to three paid orders of one shop, with their
shipments not made yet; everything else after the payment has run. Setup
draws the carrier account's duplicate guard and tracking mode, which
buyers give an address the carrier refuses, and up to two carrier faults
planned for the run. Actions: run a delivery; the worker catches up; crash
around an effect's call, and around a booking while one is due; crash
inside a local step before its commit; a handled fact met again by its
consumer; script a carrier fault; a booking's answer lost while the worker
runs on; a booking's answer lost and the carrier pushing the label's first
event before we know it; the carrier releasing a label's next events, the
rest, or a later step out of turn, pushed once, twice or badly signed;
failed pushes redelivered; a tracking page fetched (thin and poll accounts,
standing in for `carrier_poll`, step 5); refuse the next connection; a
person books a failed or stuck shipment again; the clock moves.

After every action, the payment rules (payment_checks.py) and the label
rules: a carrier that keeps keys, or none, never holds two labels for one
shipment; only a `compensable` one may hold extras, and only while the
shipment is booking or sorting them out; a labelled shipment's label is
live at the carrier, with both documents stored; a failed or stuck one has
no live label; and the tracking rule (tracking_checks.py): an event
reaches a shipment only if it is its kept label's. After draining with
faults off, and a person booking every stuck shipment again: every
shipment is labelled with exactly one live label, or its address was
refused; then, failed pushes redelivered and a page fetched for every
label, every event the carrier released for a kept label reached its
shipment, and under pushes every other label's event is kept unmatched.
"""

import asyncio
import os
from collections import Counter
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta

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

from mor.carriers.accounts import create_carrier_account
from mor.carriers.records import CarrierProvider, CarrierTrackingMode, PriceExpiry
from mor.checkout import addresses
from mor.db.connect import Connection, connect
from mor.db.fetch import one_value
from mor.fakes.carrier import SCRIPTS, Script
from mor.ids import CarrierAccountId, CarrierCode, ShipmentId
from mor.messaging.delivery import Candidate
from mor.messaging.guard import DuplicateGuard
from mor.messaging.routing import ConsumerName, Effect, Local, consumer_kind
from mor.messaging.worker import Failed, find_candidates, run_one
from mor.payments.flow import Paying, pay
from mor.provider_accounts import ProviderAccountKind
from mor.psp.port import CaptureMode, StatusMode
from mor.seed import carrier_flavour
from mor.shipments.flow import book_again
from mor.shipments.inbound import store_inbox
from tests.flow import reach
from tests.flow.payment_checks import (
    check_drained,
    check_every_action,
    crash_in_a_local_step,
    handled_locally,
    meet_again,
)
from tests.flow.tracking_checks import (
    check_events_routed,
    check_tracking_drained,
    tracking_reached,
)
from tests.helpers import (
    ENV,
    TEST_URL,
    VENDOR_TIMEOUT,
    crash_around_a_call,
    empty_tables,
    wire,
)
from tests.payments.support import (
    ACTOR,
    NOW,
    SG_ADDRESS,
    SGD_PRICE,
    Wiring,
    make_shop,
    quoted_session,
)


T0 = NOW
# What each carrier operation can be scripted to do (mor.fakes.carrier).
FAULTS = (
    ("book", "fail_500"),
    ("book", "fail_500_after_work"),
    ("book", "timeout_after_work"),
    ("find", "fail_500"),
    ("find", "timeout_after_work"),
    ("cancel", "fail_500"),
    ("cancel", "fail_500_after_work"),
    ("cancel", "timeout_after_work"),
)
REFUSED_ADDRESS = SG_ADDRESS | {addresses.LINE1: "REFUSE 1 Example Road"}
TRACKING_MODES = (
    CarrierTrackingMode.PUSH_FAT,
    CarrierTrackingMode.PUSH_THIN,
    CarrierTrackingMode.POLL,
)
PUSHES = ("once", "twice", "bad_signature")
THEN = ("nothing", "decode", "decode and match", "decode and book")
WEBHOOK_SECRET = "whsec-carrier-fuzz"
# Not run while the deep start pays: the run starts before them.
HELD_BACK = (ConsumerName.SHIPMENT_CREATE, ConsumerName.CARRIER_BOOK)

# What a run should reach before its final drain: the facts in its outbox,
# and what its actions met on the way.
MILESTONES = (
    "shipment_label_booked",
    "shipment_label_failed",
    "shipment_needs_person",
    "shipment_booking_retried",
    "labelled: vendor_constraint",
    "labelled: compensable",
    "labelled: none",
    "an extra label cancelled",
    "several shipments",
    "crash around a booking",
    "a booking answer lost",
    "crash in shipment_create",
    "met again: shipment_create",
    # Tracking (docs/plan-shipments.md §7).
    "tracked by push_fat",
    "tracked by push_thin",
    "tracked by poll",
    "an event pushed before its label was ours",
    "an event before its label was ours, matched",
    "an event's match before its label",
    "matched by both sides",
    "an extra label's event kept unmatched",
    "a push sent twice",
    "a badly signed push",
    "a step released out of turn",
    "a page fetched",
    "crash in carrier_inbox_decode",
    "crash in carrier_event_match",
    "met again: carrier_inbox_decode",
    "met again: carrier_event_match",
)
TALLY: Counter[str] = Counter()


@dataclass(frozen=True, kw_only=True)
class _FakeLabel:
    """A live label at the fake carrier, with the steps it released."""

    tracking_number: str
    script: str
    released: list[int]


def _script(text: str) -> Script:
    for script in SCRIPTS:
        if script == text:
            return script
    raise AssertionError(f"unknown script {text!r}")


def _unreleased(label: _FakeLabel) -> list[int]:
    steps: list[int] = []
    for step in range(len(SCRIPTS[_script(label.script)])):
        if step not in label.released:
            steps.append(step)
    return steps


@dataclass(frozen=True)
class _Tracked:
    tracking_number: str


@dataclass(frozen=True, kw_only=True)
class _LabelCount:
    """One shipment beside what the fake carrier holds for it."""

    id: ShipmentId
    booking_state: str
    tracking_number: str | None
    labels: int
    live: int
    ours_live: int  # live labels with our tracking number: 0 or 1
    documents: int


async def _label_counts(conn: Connection) -> list[_LabelCount]:
    async with conn.cursor(row_factory=class_row(_LabelCount)) as cur:
        await cur.execute(
            """
            SELECT s.id, s.booking_state::text AS booking_state,
                   s.tracking_number,
                   count(c.tracking_number)::int AS labels,
                   (count(c.tracking_number)
                     FILTER (WHERE c.state = 'live'))::int AS live,
                   (count(c.tracking_number)
                     FILTER (WHERE c.state = 'live'
                             AND c.tracking_number = s.tracking_number))::int
                     AS ours_live,
                   (SELECT count(*) FROM shipment_document d
                    WHERE d.shipment_id = s.id)::int AS documents
            FROM shipment s
            LEFT JOIN fake_vendor.carrier_shipment c ON c.reference = s.id::text
            GROUP BY s.id ORDER BY s.id
            """
        )
        return await cur.fetchall()


async def check_labels(conn: Connection, guard: DuplicateGuard) -> None:
    for shipment in await _label_counts(conn):
        match guard:
            case DuplicateGuard.VENDOR_CONSTRAINT | DuplicateGuard.NONE:
                # Its key replays a booking, or it is never sent again
                # unless a person saw no label: never a second label.
                assert shipment.labels <= 1, shipment
            case DuplicateGuard.COMPENSABLE:
                if shipment.booking_state not in ("booking", "deduplicating"):
                    assert shipment.live <= 1, shipment
        match shipment.booking_state:
            case "labelled":
                assert shipment.ours_live == 1, shipment
                assert shipment.documents == 2, shipment
            case "label_failed" | "needs_person":
                assert shipment.live == 0, shipment
                assert shipment.documents == 0, shipment
            case _:
                assert shipment.documents == 0, shipment


class ShipmentFlow(RuleBasedStateMachine):
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
        self.guard = DuplicateGuard.VENDOR_CONSTRAINT
        self.mode = CarrierTrackingMode.POLL
        self.account_id: CarrierAccountId | None = None
        self.base = ""
        self.orders = 0
        self.refused = 0
        self.met: set[str] = set()

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            if self.orders:
                reach.record(TALLY, self.run(self._reached()))
                self.run(self._drain_and_check())
        finally:
            self.run(self.http.aclose())
            self.run(self.control.aclose())
            self.run(self.worker.close())
            self.loop.close()

    # --- the deep start --------------------------------------------------

    @initialize(
        # `compensable` twice: it has the most machinery (deduplicating).
        guard=strategies.sampled_from(
            (
                DuplicateGuard.VENDOR_CONSTRAINT,
                DuplicateGuard.COMPENSABLE,
                DuplicateGuard.COMPENSABLE,
                DuplicateGuard.NONE,
            )
        ),
        refused=strategies.lists(
            strategies.sampled_from((False, False, False, True)),
            min_size=1,
            max_size=3,
        ),
        planned=strategies.lists(strategies.sampled_from(FAULTS), max_size=2),
        mode=strategies.sampled_from(TRACKING_MODES),
    )
    def paid_orders(
        self,
        guard: DuplicateGuard,
        refused: list[bool],
        planned: list[tuple[str, str]],
        mode: CarrierTrackingMode,
    ) -> None:
        """One paid order per entry of `refused`, whose buyer gave an
        address the carrier refuses if true. Everything after each payment
        has run but the shipment. `planned`: faults waiting at the carrier
        for their operation's first call, decided for the whole run
        (docs/plan-architecture.md §10). `mode`: how the carrier tells us
        what happens to a label, its webhook subscribed if it pushes."""
        self.guard = guard
        self.mode = mode
        self.base = f"http://mor/_fake/carrier/{carrier_flavour(guard)}/acct"

        async def step() -> None:
            shop = await make_shop(
                TEST_URL,
                self.control,
                Wiring(
                    status_mode=StatusMode.PUSH_FAT,
                    capture_mode=CaptureMode.SALE,
                    guard=DuplicateGuard.VENDOR_CONSTRAINT,
                ),
            )
            async with await connect(TEST_URL) as conn:
                self.account_id = await create_carrier_account(
                    conn,
                    CarrierCode("carrier"),
                    CarrierProvider.FAKE,
                    ProviderAccountKind.FAKE,
                    self.base,
                    VENDOR_TIMEOUT,
                    timedelta(hours=24),
                    PriceExpiry.CARRIER_FIRST,
                    book_guard=guard,
                    tracking_mode=mode,
                    webhook_secret=WEBHOOK_SECRET,
                )
                await conn.commit()
            match mode:
                case CarrierTrackingMode.PUSH_FAT | CarrierTrackingMode.PUSH_THIN:
                    subscribed = await self.control.post(
                        f"{self.base}/webhooks",
                        json={
                            "url": f"http://mor/webhooks/carrier/{self.account_id}",
                            "secret": WEBHOOK_SECRET,
                            "payload": (
                                "fat"
                                if mode == CarrierTrackingMode.PUSH_FAT
                                else "thin"
                            ),
                        },
                    )
                    assert subscribed.status_code == 204, subscribed.text
                case CarrierTrackingMode.POLL:
                    pass
            for refuse in refused:
                buyer = REFUSED_ADDRESS if refuse else SG_ADDRESS
                session_id, option = await quoted_session(
                    TEST_URL, shop.merchant_id, SGD_PRICE, self.now, buyer=buyer
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
                await self._catch_up_but_shipping()
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
                await self._catch_up_but_shipping()
            self.orders = len(refused)
            self.refused = sum(refused)
            for operation, fault in planned:
                await self._script_fault(operation, fault)

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
        self.run(self._catch_up())

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
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @precondition(lambda self: self.run(self._bookings_due()) != [])
    @rule(data=strategies.data(), with_call=strategies.booleans())
    def crash_around_a_booking(
        self, data: strategies.DataObject, with_call: bool
    ) -> None:
        """Only drawn while a booking is due: the lost answer is the case
        the duplicate guard exists for."""
        candidate = data.draw(strategies.sampled_from(self.run(self._bookings_due())))
        self.met.add("crash around a booking")
        self.run(
            crash_around_a_call(candidate, with_call, self.http, self.network, self.now)
        )

    @precondition(lambda self: self.run(self._bookings_due()) != [])
    @rule()
    def a_booking_answer_is_lost_and_the_worker_runs_on(self) -> None:
        """The carrier books and never answers, and the worker carries on:
        the resend, and on a `compensable` carrier the extra label and its
        cancel, all within the run where the other actions can meet them."""
        self.met.add("a booking answer lost")
        self.run(self._script_fault("book", "timeout_after_work"))
        self.run(self._catch_up())

    @precondition(lambda self: self.run(self._bookings_due()) != [])
    @rule(data=strategies.data(), then=strategies.sampled_from(THEN))
    def a_booking_answer_is_lost_and_the_carrier_pushes_first(
        self, data: strategies.DataObject, then: str
    ) -> None:
        """The carrier books and never answers; before the booking is sent
        again, the parcel is picked up and the carrier says so. Under a
        `compensable` guard the label may never be ours: the resend's is
        kept. `then` builds the orders the match must survive
        (docs/plan-shipments.md §7): `nothing` leaves the rest to the other
        actions; `decode` stores the event unmatched, its match still due;
        `decode and match` also runs that match, which finds no shipment, so
        only the label's own match can take the event on; `decode and book`
        sends the booking again instead, so both sides' matches are due once
        both have committed."""
        candidate = data.draw(strategies.sampled_from(self.run(self._bookings_due())))
        self.met.add("a booking answer lost")
        self.run(self._script_fault("book", "timeout_after_work"))
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result
        for tracking_number in self.run(self._labels_not_ours(candidate)):
            if self.mode != CarrierTrackingMode.POLL:
                self.met.add("an event pushed before its label was ours")
            self.run(self._release({"tracking_number": tracking_number}))
        if then == "nothing":
            return
        self.run(self._run_only(ConsumerName.CARRIER_INBOX_DECODE))
        match then:
            case "decode and match":
                if self.mode != CarrierTrackingMode.POLL:
                    self.met.add("an event's match before its label")
                self.run(self._run_only(ConsumerName.CARRIER_EVENT_MATCH))
            case "decode and book":
                self.run(self._book_when_due())
            case _:
                pass

    @rule(
        data=strategies.data(),
        how=strategies.sampled_from(("next", "next", "all", "later")),
        push=strategies.sampled_from(PUSHES),
    )
    def the_carrier_releases_events(
        self, data: strategies.DataObject, how: str, push: str
    ) -> None:
        """What happened to a live label: its next step, the rest of its
        script, or a later step out of turn; pushed once, twice or badly
        signed on a pushing account."""
        labels = self.run(self._labels_with_news())
        if not labels:
            return
        label = data.draw(strategies.sampled_from(labels))
        steps = _unreleased(label)
        body: dict[str, object] = {"tracking_number": label.tracking_number}
        if how == "all":
            body["all"] = True
        elif how == "later" and len(steps) > 1:
            body["step"] = data.draw(strategies.sampled_from(steps[1:]))
            self.met.add("a step released out of turn")
        if self.mode != CarrierTrackingMode.POLL:
            body["push"] = push
            if push == "twice":
                self.met.add("a push sent twice")
            if push == "bad_signature":
                self.met.add("a badly signed push")
        self.run(self._release(body))

    @precondition(lambda self: self.mode != CarrierTrackingMode.POLL)
    @rule()
    def the_carrier_redelivers_failed_pushes(self) -> None:
        self.run(self._redeliver())

    @precondition(lambda self: self.mode != CarrierTrackingMode.PUSH_FAT)
    @rule(data=strategies.data())
    def a_tracking_page_is_fetched(self, data: strategies.DataObject) -> None:
        """A labelled shipment's events fetched by its tracking number, as
        `carrier_poll` will (step 5) on a poll and on a thin notice."""
        tracked = self.run(self._tracked())
        if not tracked:
            return
        self.met.add("a page fetched")
        self.run(self._fetch_page(data.draw(strategies.sampled_from(tracked))))

    @rule(data=strategies.data())
    def a_consumer_meets_its_fact_again(self, data: strategies.DataObject) -> None:
        """See payment_checks.meet_again."""
        handled = self.run(handled_locally(self.worker))
        if not handled:
            return
        chosen = data.draw(strategies.sampled_from(handled))
        self.met.add(f"met again: {chosen.consumer}")
        self.run(meet_again(chosen, self.now))

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(planned=strategies.sampled_from(FAULTS))
    def script_a_fault(self, planned: tuple[str, str]) -> None:
        operation, fault = planned
        self.run(self._script_fault(operation, fault))

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1

    @rule(data=strategies.data())
    def a_person_books_again(self, data: strategies.DataObject) -> None:
        stuck = self.run(self._stuck())
        if not stuck:
            return
        self.run(self._book_again(data.draw(strategies.sampled_from(stuck))))

    @rule(seconds=strategies.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    # --- checks ----------------------------------------------------------

    @invariant()
    def the_payment_and_label_rules_hold(self) -> None:
        self.run(check_every_action(self.worker))
        self.run(check_labels(self.worker, self.guard))
        self.run(check_events_routed(self.worker))

    # --- helpers ---------------------------------------------------------

    async def _catch_up_but_shipping(self) -> None:
        for _ in range(100):
            due: list[Candidate] = []
            for candidate in await find_candidates(self.worker, self.now, 50):
                if candidate.consumer not in HELD_BACK:
                    due.append(candidate)
            if not due:
                return
            for candidate in due:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        raise AssertionError("the worker never caught up")

    async def _catch_up(self) -> None:
        for _ in range(100):
            candidates = await find_candidates(self.worker, self.now, 50)
            if not candidates:
                return
            for candidate in candidates:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        raise AssertionError("the worker never caught up")

    async def _script_fault(self, operation: str, fault: str) -> None:
        response = await self.control.post(
            "http://mor/_fake/carrier/faults",
            json={"operation": operation, "fault": fault},
        )
        assert response.status_code == 204, response.text

    async def _faults_waiting(self) -> int:
        return await one_value(
            self.worker,
            "SELECT count(*)::int FROM fake_vendor.carrier_fault",
            None,
            int,
        )

    async def _stuck(self) -> list[ShipmentId]:
        cur = await self.worker.execute(
            "SELECT id FROM shipment"
            " WHERE booking_state IN ('label_failed', 'needs_person') ORDER BY id"
        )
        return [ShipmentId(row[0]) for row in await cur.fetchall()]

    async def _book_again(self, shipment_id: ShipmentId) -> None:
        async with await connect(TEST_URL) as conn:
            await book_again(conn, shipment_id, ACTOR, self.now)
            await conn.commit()

    def _draw(
        self, data: strategies.DataObject, kind: type[Local] | type[Effect] | None
    ) -> Candidate | None:
        found = self.run(find_candidates(self.worker, self.now, 50))
        if kind is not None:
            found = [c for c in found if isinstance(consumer_kind(c.consumer), kind)]
        if not found:
            return None
        return data.draw(strategies.sampled_from(found))

    async def _bookings_due(self) -> list[Candidate]:
        found: list[Candidate] = []
        for candidate in await find_candidates(self.worker, self.now, 50):
            if candidate.consumer == ConsumerName.CARRIER_BOOK:
                found.append(candidate)
        return found

    async def _run_only(self, consumer: ConsumerName) -> None:
        """Run the due deliveries of one consumer only, once each."""
        for candidate in await find_candidates(self.worker, self.now, 50):
            if candidate.consumer != consumer:
                continue
            result = await run_one(self.worker, self.http, ENV, candidate, self.now)
            assert not isinstance(result, Failed), result

    async def _book_when_due(self) -> None:
        """The clock to the open bookings' retry, never back, and only the
        bookings run."""
        due = await one_value(
            self.worker,
            "SELECT coalesce(max(not_before), %s) FROM outbox_delivery"
            " WHERE consumer = 'carrier_book' AND handled_at IS NULL",
            (self.now,),
            datetime,
        )
        self.now = max(self.now, due)
        await self._run_only(ConsumerName.CARRIER_BOOK)

    async def _release(self, body: dict[str, object]) -> None:
        response = await self.control.post(f"{self.base}/events/release", json=body)
        assert response.status_code == 200, response.text

    async def _redeliver(self) -> None:
        response = await self.control.post(f"{self.base}/events/redeliver", json={})
        assert response.status_code == 200, response.text

    async def _fetch_page(self, tracking_number: str) -> None:
        page = await self.control.get(
            f"{self.base}/tracking", params={"tracking_number": tracking_number}
        )
        assert page.status_code == 200, page.text
        assert self.account_id is not None
        async with await connect(TEST_URL) as conn:
            await store_inbox(
                conn, self.account_id, CarrierTrackingMode.POLL, page.content, self.now
            )
            await conn.commit()

    async def _labels_with_news(self) -> list[_FakeLabel]:
        """Live labels at the fake with steps not released yet, ours or not
        (an extra label, one whose booking answer was lost)."""
        async with self.worker.cursor(row_factory=class_row(_FakeLabel)) as cur:
            await cur.execute(
                """
                SELECT c.tracking_number, c.script::text AS script,
                       array(SELECT e.step FROM fake_vendor.carrier_track_event e
                             WHERE e.tracking_number = c.tracking_number
                             ORDER BY e.step) AS released
                FROM fake_vendor.carrier_shipment c
                WHERE c.account = 'acct' AND c.state = 'live'
                ORDER BY c.tracking_number
                """
            )
            labels = await cur.fetchall()
        with_news: list[_FakeLabel] = []
        for label in labels:
            if _unreleased(label):
                with_news.append(label)
        return with_news

    async def _labels_not_ours(self, candidate: Candidate) -> list[str]:
        """Live labels the fake holds for the booking's shipment that no
        shipment has as its own (yet)."""
        async with self.worker.cursor(row_factory=class_row(_Tracked)) as cur:
            await cur.execute(
                """
                SELECT c.tracking_number FROM fake_vendor.carrier_shipment c
                WHERE c.reference = %s AND c.state = 'live'
                  AND NOT EXISTS (SELECT 1 FROM shipment s
                                  WHERE s.tracking_number = c.tracking_number)
                  AND NOT EXISTS (SELECT 1 FROM fake_vendor.carrier_track_event e
                                  WHERE e.tracking_number = c.tracking_number)
                ORDER BY c.tracking_number
                """,
                (str(candidate.aggregate_id),),
            )
            found = await cur.fetchall()
        numbers: list[str] = []
        for label in found:
            numbers.append(label.tracking_number)
        return numbers

    async def _tracked(self) -> list[str]:
        """The tracking numbers of our kept labels."""
        async with self.worker.cursor(row_factory=class_row(_Tracked)) as cur:
            await cur.execute(
                "SELECT tracking_number FROM shipment"
                " WHERE tracking_number IS NOT NULL ORDER BY tracking_number"
            )
            found = await cur.fetchall()
        numbers: list[str] = []
        for shipment in found:
            numbers.append(shipment.tracking_number)
        return numbers

    async def _reached(self) -> set[str]:
        cur = await self.worker.execute(
            "SELECT DISTINCT message_type::text FROM outbox"
        )
        reached = {row[0] for row in await cur.fetchall()} & set(MILESTONES)
        labelled = await one_value(
            self.worker,
            "SELECT count(*)::int FROM shipment WHERE booking_state = 'labelled'",
            None,
            int,
        )
        if labelled:
            reached.add(f"labelled: {self.guard.value}")
        cancelled = await one_value(
            self.worker,
            "SELECT count(*)::int FROM carrier_request"
            " WHERE operation = 'cancel' AND state = 'succeeded'",
            None,
            int,
        )
        if cancelled:
            reached.add("an extra label cancelled")
        if self.orders >= 2:
            reached.add("several shipments")
        reached |= await tracking_reached(self.worker, self.mode)
        return reached | (self.met & set(MILESTONES))

    async def _drain_and_check(self) -> None:
        await self.worker.execute("DELETE FROM fake_vendor.carrier_fault")
        self.network.refuse_next = 0
        for _ in range(10):
            self.now += timedelta(minutes=5)
            await self._catch_up()
        await check_every_action(self.worker)
        await check_labels(self.worker, self.guard)
        await check_drained(self.worker)
        # Not one shipment left booking; a person books the stuck ones again
        # (on a carrier without keys, after checking it holds no label).
        for stuck in await self._stuck():
            await self._book_again(stuck)
        for _ in range(10):
            self.now += timedelta(minutes=5)
            await self._catch_up()
        await check_labels(self.worker, self.guard)
        counts = await _label_counts(self.worker)
        assert len(counts) == self.orders, counts
        failed = 0
        for shipment in counts:
            match shipment.booking_state:
                case "labelled":
                    assert shipment.live == 1, shipment
                case "label_failed":
                    failed += 1
                case _:
                    raise AssertionError(f"left {shipment.booking_state}: {shipment}")
        # Only a refused address fails for good.
        assert failed == self.refused, counts

        # What the carrier said reaches us: pushes that failed are sent
        # again, and every kept label's page is fetched.
        if self.mode != CarrierTrackingMode.POLL:
            await self._redeliver()
        if self.mode != CarrierTrackingMode.PUSH_FAT:
            for tracking_number in await self._tracked():
                await self._fetch_page(tracking_number)
        for _ in range(3):
            self.now += timedelta(minutes=5)
            await self._catch_up()
        await check_events_routed(self.worker)
        await check_tracking_drained(self.worker, self.mode)


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_every_paid_order_gets_one_label_and_its_events_through_faults_and_crashes(
    migrated_test_database: str,
) -> None:
    TALLY.clear()
    run_state_machine_as_test(
        ShipmentFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
    reach.finish("shipment-flow", MILESTONES, TALLY)
