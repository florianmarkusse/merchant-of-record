"""The duty lookup flow as a Hypothesis state machine against mor_test and
the fake duty vendor in-process (docs/plan-quote.md §3, 4b-2).

Actions: ask about a combination (also one already asked); run a
delivery; the worker catches up; crash after `prepare` committed, with or
without the call made; script a vendor fault; refuse the next connection;
redeliver a handled message; move the clock.

After every action: one `duty_rate_needed` per lookup; a lookup holds
answers exactly when it is answered, one per charge; the vendor was never
called more often than we counted; every counted call beyond the first
per lookup has a cause (a fault that loses the answer, a refused
connection, or a crash); and a lookup ends without an answer only after
a fault that ends it. After draining with faults off: no lookup is still
waiting.
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
from mor.db.connect import Connection, connect
from mor.duties.lookups import create_vendor_account, request_duty_rate
from mor.duties.port import DutyProvider, RateQuestion
from mor.markets.source import parse
from mor.markets.store import load_market
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
from mor.provider_accounts import ProviderAccountKind
from tests.helpers import (
    ENV,
    TEST_URL,
    VENDOR_TIMEOUT,
    crash,
    effect_consumer,
    empty_tables,
    wire,
)
from tests.markets.fixtures import SG_GST, TH_BORDER, market_texts


T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
QUESTIONS = (
    RateQuestion("TH", "VN", "610910"),
    RateQuestion("TH", "CN", "610910"),
    RateQuestion("SG", "VN", "620342"),
)
# Faults after which the answer is lost, so the lookup is asked again.
LOSING = ("fail_500", "timeout_after_work")
ENDING = ("unknown_code", "undecodable")
ANSWERING = ("specific_duty", "preference_note")
TH_HS = "TH,6109,duty,,,,30,,secondary,WTO,https://example.test,,,\n"


class DutyLookupFlow(RuleBasedStateMachine):
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
        self.lookups: dict[RateQuestion, uuid.UUID] = {}
        # What was scripted; what was used is that minus what still waits
        # (fault_kinds_waiting, network.refuse_next), so the bounds below
        # stay tight however many are scripted.
        self.scripted: dict[str, int] = {}
        self.refusals_scripted = 0
        # Crashes after `prepare` counted a call.
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
    def a_first_combination_is_asked(self, question: RateQuestion) -> None:
        """So a delivery is there to crash or fault from the first step: a
        crash right after the first ask was missed at the dev budget
        without it (18-Nov-2026)."""
        self.ask_about_a_combination(question)

    @rule(question=st.sampled_from(QUESTIONS))
    def ask_about_a_combination(self, question: RateQuestion) -> None:
        async def step() -> uuid.UUID:
            async with await connect(TEST_URL) as conn:
                asked = await request_duty_rate(conn, "acme", question, ACTOR, self.now)
                await conn.commit()
            assert isinstance(asked, uuid.UUID)
            return asked

        lookup_id = self.run(step())
        known = self.lookups.get(question)
        assert known is None or known == lookup_id
        self.lookups[question] = lookup_id

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
            self.control.post("http://mor/_fake/duty/faults", json={"fault": fault})
        )
        assert response.status_code == 204

    @precondition(lambda self: self.network.refuse_next < 2)
    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1
        self.refusals_scripted += 1

    @precondition(lambda self: len(self.lookups) > 0)
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

    # --- checks after every action ---------------------------------------

    @invariant()
    def each_lookup_was_needed_once(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT l.id, count(o.id) FROM duty_lookup l"
                " LEFT JOIN outbox o ON o.aggregate_id = l.id"
                " AND o.message_type = 'duty_rate_needed' GROUP BY l.id"
            )
        )
        rows = self.run(cur.fetchall())
        assert len(rows) == len(self.lookups)
        for lookup_id, needed in rows:
            assert needed == 1, lookup_id

    @invariant()
    def answers_exist_exactly_for_answered_lookups(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT l.state::text, count(a.charge) FROM duty_lookup l"
                " LEFT JOIN duty_answer a ON a.lookup_id = l.id GROUP BY l.id"
            )
        )
        for state, answers in self.run(cur.fetchall()):
            assert answers == (2 if state == "answered" else 0), (state, answers)

    @invariant()
    def the_vendor_is_never_called_more_than_we_counted(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT l.calls, (SELECT count(*) FROM fake_vendor.duty_call c"
                "   WHERE c.destination = l.destination AND c.origin = l.origin"
                "   AND c.hs_code = l.hs_code)"
                " FROM duty_lookup l"
            )
        )
        extra = 0
        for counted, made in self.run(cur.fetchall()):
            assert made <= counted, (made, counted)
            extra += max(counted - 1, 0)
        causes = self.run(self._used(LOSING)) + self.counted_crashes
        causes += self.refusals_scripted - self.network.refuse_next
        assert extra <= causes, (extra, causes)

    @invariant()
    def a_lookup_ends_unanswered_only_after_such_a_fault(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM duty_lookup"
                " WHERE state IN ('unknown_code', 'needs_person')"
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
        market = parse(market_texts(SG_GST + TH_BORDER, hs_rows=TH_HS))
        assert not isinstance(market, list)
        async with await connect(TEST_URL) as conn:
            await load_market(conn, market, "fuzz", ACTOR, self.now)
            await create_vendor_account(
                conn,
                "acme",
                DutyProvider.FAKE,
                ProviderAccountKind.FAKE,
                "http://mor/_fake/duty/acct",
                VENDOR_TIMEOUT,
            )
            await conn.commit()

    async def _faults_waiting(self) -> int:
        cur = await self.worker.execute("SELECT count(*) FROM fake_vendor.duty_fault")
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _used(self, kinds: tuple[str, ...]) -> int:
        """Faults of these kinds the vendor has taken: scripted minus
        still waiting."""
        cur = await self.worker.execute(
            "SELECT fault::text, count(*) FROM fake_vendor.duty_fault GROUP BY fault"
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
            " WHERE handled_at IS NOT NULL AND consumer = 'duty_rate_ask'"
            " ORDER BY seq"
        )
        handled: list[Candidate] = []
        for message_id, lookup_id in await cur.fetchall():
            handled.append(
                Candidate(
                    message_id=message_id,
                    consumer=ConsumerName.DUTY_RATE_ASK,
                    aggregate_type=AggregateType.DUTY_LOOKUP,
                    aggregate_id=lookup_id,
                )
            )
        return handled

    async def _drain_and_check(self) -> None:
        # Faults off, clock far ahead: every lookup must finish.
        await self.worker.execute("DELETE FROM fake_vendor.duty_fault")
        self.network.refuse_next = 0
        for _ in range(20):
            self.now += timedelta(hours=2)
            await self._catch_up()
        cur = await self.worker.execute(
            "SELECT count(*) FROM duty_lookup WHERE state = 'requested'"
        )
        row = await cur.fetchone()
        assert row == (0,), "a lookup is still waiting after the drain"
        cur = await self.worker.execute(
            "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        row = await cur.fetchone()
        assert row == (0,), "a delivery is still open after the drain"


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_duty_lookups_survive_faults_crashes_and_lost_answers(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        DutyLookupFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
