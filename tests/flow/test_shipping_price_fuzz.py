"""The live carrier price flow as a Hypothesis state machine against
mor_test and the fake courier in-process (docs/plan-quote.md §4, 4c-2).

Actions: ask for a price (also one already open or held); run a delivery;
the worker catches up; crash after `prepare` committed, with or without
the call made; script a carrier fault; refuse the next connection;
redeliver a handled message; move the clock, now and then past a price's
period; switch the account's expiry rule.

After every action: at most one open request per combination; an ask
returns the open request or a lookup usable at that moment; one
`shipping_price_needed` per lookup; an answered lookup's parts sum to its
total, and only answered lookups have parts; the carrier was never called
more often than we counted; every counted call beyond the first per lookup
has a cause (a fault that loses the answer, a refused connection, or a
crash); and a lookup ends without a price only after a fault that ends it.
After draining with faults off: no request is still open.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta

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

from mor.audit import Actor, ActorKind
from mor.couriers.prices import (
    create_courier_account,
    read_price,
    request_shipping_price,
    set_price_rule,
)
from mor.couriers.records import (
    CourierProvider,
    Parcel,
    PriceExpiry,
    PriceQuestion,
    PriceState,
)
from mor.db.connect import Connection, connect
from mor.messaging import consumers
from mor.messaging.facts import AggregateType
from mor.messaging.routing import ConsumerName, Effect, consumer_kind
from mor.messaging.worker import (
    Candidate,
    Failed,
    Prepared,
    Skipped,
    find_candidates,
    prepare_delivery,
    run_one,
    try_lock_aggregate,
)
from tests.helpers import (
    ENV,
    TEST_URL,
    VENDOR_TIMEOUT,
    crash,
    effect_consumer,
    empty_tables,
    wire,
)


T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
VALID_FOR = timedelta(hours=6)
QUESTIONS = (
    PriceQuestion("carrier", "exp", "VN", "SG", Parcel(1000, 200, 150, 100)),
    PriceQuestion("carrier", "exp", "VN", "SG", Parcel(2500, 400, 300, 200)),
    PriceQuestion("carrier", "exp", "VN", "AU", Parcel(1000, 200, 150, 100)),
)
# Faults after which the answer is lost, so the lookup is asked again.
LOSING = ("fail_500", "timeout_after_work")
ENDING = ("not_served", "undecodable", "parts_mismatch")
ANSWERING = ("other_currency", "already_expired")


class ShippingPriceFlow(RuleBasedStateMachine):
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
        self.run(self._set_up())
        # The model: the request each question has open, as an ask saw it.
        self.open: dict[PriceQuestion, uuid.UUID] = {}
        self.scripted: dict[str, int] = {}
        self.refusals_scripted = 0
        self.counted_crashes = 0

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            self.run(self._drain_and_check())
        finally:
            self.run(self.http.aclose())
            self.run(self.control.aclose())
            self.run(self.worker.close())
            self.loop.close()

    # --- actions ---------------------------------------------------------

    @initialize(question=st.sampled_from(QUESTIONS))
    def a_first_price_is_asked(self, question: PriceQuestion) -> None:
        """So a delivery is there to crash or fault from the first step."""
        self.ask_for_a_price(question)

    @rule(question=st.sampled_from(QUESTIONS))
    def ask_for_a_price(self, question: PriceQuestion) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                asked = await request_shipping_price(conn, question, ACTOR, self.now)
                await conn.commit()
                assert isinstance(asked, uuid.UUID)
                held = await read_price(conn, question)
            if held.open_id == asked:
                known = self.open.get(question)
                # Still open since the last ask: the same request.
                assert known is None or known == asked or not await self._is_open(known)
                self.open[question] = asked
                return
            # Not open: it must be a finished lookup usable right now.
            assert held.latest is not None and held.latest.id == asked
            assert held.latest.usable_until > self.now, (held.latest, self.now)

        self.run(step())

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
        """`prepare` commits (the call counted), maybe the call is made,
        and the worker dies before `complete`."""
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        if not isinstance(consumer_kind(candidate.consumer), Effect):
            return  # a local step has no call to crash around

        async def step() -> bool:
            crashing = await connect(TEST_URL, autocommit=True)
            try:
                assert await try_lock_aggregate(crashing, candidate)
                async with crashing.transaction():
                    prepared = await prepare_delivery(
                        crashing, candidate, effect_consumer(candidate), self.now
                    )
                if with_call and isinstance(prepared, Prepared):
                    await consumers.call(self.http, ENV, prepared.call)
                return isinstance(prepared, Prepared)
            finally:
                await crash(crashing)

        if self.run(step()):
            self.counted_crashes += 1

    @precondition(lambda self: self.run(self._faults_waiting()) < 2)
    @rule(fault=st.sampled_from(LOSING + ENDING + ANSWERING))
    def script_a_fault(self, fault: str) -> None:
        self.scripted[fault] = self.scripted.get(fault, 0) + 1
        response = self.run(
            self.control.post("http://mor/_fake/courier/faults", json={"fault": fault})
        )
        assert response.status_code == 204

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1
        self.refusals_scripted += 1

    @rule(data=st.data())
    def redeliver_a_handled_message(self, data: st.DataObject) -> None:
        handled = self.run(self._handled())
        if not handled:
            return
        candidate = data.draw(st.sampled_from(handled))
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert result == Skipped("already_handled"), result

    @rule(seconds=st.integers(min_value=0, max_value=600))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    @rule()
    def clock_passes_a_period(self) -> None:
        self.now += VALID_FOR

    @rule(expiry=st.sampled_from(PriceExpiry))
    def the_expiry_rule_is_switched(self, expiry: PriceExpiry) -> None:
        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await set_price_rule(conn, "carrier", VALID_FOR, expiry)
                await conn.commit()

        self.run(step())

    # --- checks after every action ---------------------------------------

    @invariant()
    def at_most_one_request_is_open_per_combination(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM shipping_price_lookup"
                " WHERE state = 'requested'"
                " GROUP BY carrier, service, origin, destination, weight_g,"
                " length_mm, width_mm, height_mm HAVING count(*) > 1"
            )
        )
        assert self.run(cur.fetchall()) == []

    @invariant()
    def each_lookup_was_needed_once(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT l.id, count(o.id) FROM shipping_price_lookup l"
                " LEFT JOIN outbox o ON o.aggregate_id = l.id"
                " AND o.message_type = 'shipping_price_needed' GROUP BY l.id"
            )
        )
        for lookup_id, needed in self.run(cur.fetchall()):
            assert needed == 1, lookup_id

    @invariant()
    def parts_exist_only_for_prices_and_sum_to_them(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT l.state::text, l.total_minor, count(p.line_no),"
                " coalesce(sum(p.amount_minor), 0)"
                " FROM shipping_price_lookup l"
                " LEFT JOIN shipping_price_part p ON p.lookup_id = l.id GROUP BY l.id"
            )
        )
        for state, total, parts, summed in self.run(cur.fetchall()):
            if PriceState(state) == PriceState.ANSWERED:
                assert parts > 0 and summed == total, (total, summed)
            else:
                assert parts == 0, state

    @invariant()
    def the_carrier_is_never_called_more_than_we_counted(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT (SELECT coalesce(sum(l.calls), 0) FROM shipping_price_lookup l"
                "   WHERE l.service = c.service AND l.destination = c.destination"
                "   AND l.weight_g = c.weight_g AND l.length_mm = c.length_mm"
                "   AND l.width_mm = c.width_mm AND l.height_mm = c.height_mm),"
                " count(*)"
                " FROM fake_vendor.courier_call c"
                " GROUP BY c.service, c.destination, c.weight_g, c.length_mm,"
                " c.width_mm, c.height_mm"
            )
        )
        for counted, made in self.run(cur.fetchall()):
            assert made <= counted, (made, counted)
        cur = self.run(
            self.worker.execute(
                "SELECT coalesce(sum(greatest(calls - 1, 0)), 0)"
                " FROM shipping_price_lookup"
            )
        )
        row = self.run(cur.fetchone())
        assert row is not None
        causes = self.run(self._used(LOSING)) + self.counted_crashes
        causes += self.refusals_scripted - self.network.refuse_next
        assert row[0] <= causes, (row[0], causes)

    @invariant()
    def a_lookup_ends_without_a_price_only_after_such_a_fault(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM shipping_price_lookup"
                " WHERE state IN ('not_served', 'needs_person')"
            )
        )
        row = self.run(cur.fetchone())
        ending = self.run(self._used(ENDING))
        assert row is not None and row[0] <= ending, (row, ending)

    @invariant()
    def no_advisory_lock_is_left_held(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                " AND database = (SELECT oid FROM pg_database"
                " WHERE datname = current_database())"
            )
        )
        row = self.run(cur.fetchone())
        assert row is not None and row[0] == 0

    # --- helpers ---------------------------------------------------------

    async def _set_up(self) -> None:
        async with await connect(TEST_URL) as conn:
            await create_courier_account(
                conn,
                "carrier",
                CourierProvider.FAKE,
                "http://mor/_fake/courier/acct",
                VENDOR_TIMEOUT,
                VALID_FOR,
                PriceExpiry.CARRIER_FIRST,
            )
            await conn.commit()

    async def _is_open(self, lookup_id: uuid.UUID) -> bool:
        cur = await self.worker.execute(
            "SELECT state::text FROM shipping_price_lookup WHERE id = %s",
            (lookup_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return PriceState(row[0]) == PriceState.REQUESTED

    async def _faults_waiting(self) -> int:
        cur = await self.worker.execute(
            "SELECT count(*) FROM fake_vendor.courier_fault"
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _used(self, kinds: tuple[str, ...]) -> int:
        """Faults of these kinds the carrier has taken: scripted minus
        still waiting."""
        cur = await self.worker.execute(
            "SELECT fault::text, count(*) FROM fake_vendor.courier_fault GROUP BY fault"
        )
        waiting: dict[str, int] = {}
        for fault, count in await cur.fetchall():
            waiting[fault] = count
        used = 0
        for kind in kinds:
            used += self.scripted.get(kind, 0) - waiting.get(kind, 0)
        return used

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

    async def _handled(self) -> list[Candidate]:
        cur = await self.worker.execute(
            "SELECT message_id, aggregate_id FROM outbox_delivery"
            " WHERE handled_at IS NOT NULL AND consumer = 'shipping_price_ask'"
            " ORDER BY seq"
        )
        handled: list[Candidate] = []
        for message_id, lookup_id in await cur.fetchall():
            handled.append(
                Candidate(
                    message_id,
                    ConsumerName.SHIPPING_PRICE_ASK,
                    AggregateType.SHIPPING_PRICE_LOOKUP,
                    lookup_id,
                )
            )
        return handled

    async def _drain_and_check(self) -> None:
        # Faults off, clock ahead: every request must finish.
        await self.worker.execute("DELETE FROM fake_vendor.courier_fault")
        self.network.refuse_next = 0
        for _ in range(20):
            self.now += timedelta(hours=2)
            await self._catch_up()
        cur = await self.worker.execute(
            "SELECT count(*) FROM shipping_price_lookup WHERE state = 'requested'"
        )
        row = await cur.fetchone()
        assert row == (0,), "a request is still open after the drain"
        cur = await self.worker.execute(
            "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        row = await cur.fetchone()
        assert row == (0,), "a delivery is still open after the drain"


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_shipping_prices_survive_faults_crashes_expiry_and_lost_answers(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        ShippingPriceFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
