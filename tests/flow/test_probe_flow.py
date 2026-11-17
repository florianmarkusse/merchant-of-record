"""Layer 2 (plan-architecture.md §10): the probe flow as a Hypothesis state
machine against mor_test.

Hypothesis picks the next action: start a probe, run a delivery, crash in
the middle of one (the connection dies before COMMIT), crash right after
taking the aggregate's lock, redeliver a handled message, repeat a fact as
a retrying producer would, move the clock. After every action the database
must be in a state the model allows; after draining, every probe is
finished and every effect happened exactly once.

`RuleBasedStateMachine` needs inheritance: allowed in tests only. The
machine owns one event loop and runs our async steps on it.
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
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection, connect
from mor.messaging.facts import AggregateType, ProbeStartedV1
from mor.messaging.routing import ConsumerName
from mor.messaging.store import append_fact
from mor.messaging.worker import (
    Candidate,
    Failed,
    Skipped,
    find_candidates,
    handle_delivery,
    run_one,
    try_lock_aggregate,
)
from mor.probe.flow import ProbeState, start_probe
from tests.helpers import ENV, NO_HTTP, TEST_URL, empty_tables


T0 = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
ORDER = [ProbeState.STARTED, ProbeState.ECHOED, ProbeState.FINISHED]


class ProbeFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.now = T0
        # The model: the furthest state each probe was seen in. States
        # only move forward.
        self.seen: dict[uuid.UUID, ProbeState] = {}

    def run[T](self, step: Coroutine[object, object, T]) -> T:
        return self.loop.run_until_complete(step)

    def teardown(self) -> None:
        try:
            self.run(self._drain_and_check())
        finally:
            self.run(self.worker.close())
            self.loop.close()

    # --- actions ---------------------------------------------------------

    @rule()
    def start(self) -> None:
        async def step() -> uuid.UUID:
            async with await connect(TEST_URL) as conn:
                probe_id = await start_probe(conn, ACTOR, self.now)
                await conn.commit()
            return probe_id

        self.seen[self.run(step())] = ProbeState.STARTED

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return
        candidate = data.draw(st.sampled_from(candidates))
        result = self.run(run_one(self.worker, NO_HTTP, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule(data=st.data())
    def run_any_open_delivery(self, data: st.DataObject) -> None:
        # Not only the oldest per aggregate: a worker with an out-of-date
        # candidate list tries whatever it saw.
        open_deliveries = self.run(self._deliveries(handled=False))
        if not open_deliveries:
            return
        candidate = data.draw(st.sampled_from(open_deliveries))
        result = self.run(run_one(self.worker, NO_HTTP, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule(data=st.data())
    def crash_mid_delivery(self, data: st.DataObject) -> None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return
        candidate = data.draw(st.sampled_from(candidates))

        async def step() -> None:
            crashing = await connect(TEST_URL, autocommit=True)
            assert await try_lock_aggregate(crashing, candidate)
            await crashing.execute("BEGIN")
            await handle_delivery(crashing, candidate, self.now)
            await crashing.close()  # dies before COMMIT

        self.run(step())

    @rule(data=st.data())
    def crash_holding_the_lock(self, data: st.DataObject) -> None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return
        candidate = data.draw(st.sampled_from(candidates))

        async def step() -> None:
            crashing = await connect(TEST_URL, autocommit=True)
            assert await try_lock_aggregate(crashing, candidate)
            await crashing.close()

        self.run(step())

    @rule(data=st.data())
    def redeliver_a_handled_message(self, data: st.DataObject) -> None:
        handled = self.run(self._deliveries(handled=True))
        if not handled:
            return
        candidate = data.draw(st.sampled_from(handled))
        result = self.run(run_one(self.worker, NO_HTTP, ENV, candidate, self.now))
        assert result == Skipped("already_handled"), result

    @precondition(lambda self: len(self.seen) > 0)
    @rule(data=st.data())
    def producer_repeats_a_fact(self, data: st.DataObject) -> None:
        probe_id = data.draw(st.sampled_from(sorted(self.seen)))

        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                await append_fact(
                    conn, ProbeStartedV1(probe_id=probe_id), ACTOR, self.now
                )
                await conn.commit()

        self.run(step())

    @rule(seconds=st.integers(min_value=0, max_value=120))
    def clock_moves(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)

    # --- checks after every action ---------------------------------------

    @invariant()
    def states_only_move_forward(self) -> None:
        states = self.run(self._states())
        assert set(states) == set(self.seen)
        for probe_id, state in states.items():
            assert ORDER.index(state) >= ORDER.index(self.seen[probe_id])
            self.seen[probe_id] = state

    @invariant()
    def each_probe_is_echoed_at_most_once(self) -> None:
        for count in self.run(self._echo_counts()).values():
            assert count <= 1

    @invariant()
    def no_advisory_lock_is_left_held(self) -> None:
        assert self.run(self._advisory_locks()) == 0

    # --- queries ---------------------------------------------------------

    async def _states(self) -> dict[uuid.UUID, ProbeState]:
        cur = await self.worker.execute("SELECT id, state::text FROM probe")
        states: dict[uuid.UUID, ProbeState] = {}
        for probe_id, state in await cur.fetchall():
            states[probe_id] = ProbeState(state)
        return states

    async def _echo_counts(self) -> dict[uuid.UUID, int]:
        cur = await self.worker.execute(
            "SELECT aggregate_id, count(*) FROM outbox"
            " WHERE message_type = 'probe_echoed' GROUP BY aggregate_id"
        )
        counts: dict[uuid.UUID, int] = {}
        for probe_id, count in await cur.fetchall():
            counts[probe_id] = count
        return counts

    async def _advisory_locks(self) -> int:
        cur = await self.worker.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    async def _deliveries(self, handled: bool) -> list[Candidate]:
        cur = await self.worker.execute(
            "SELECT message_id, consumer::text, aggregate_id FROM outbox_delivery"
            " WHERE (handled_at IS NOT NULL) = %s ORDER BY seq, consumer",
            (handled,),
        )
        deliveries: list[Candidate] = []
        for message_id, consumer, aggregate_id in await cur.fetchall():
            deliveries.append(
                Candidate(
                    message_id,
                    ConsumerName(consumer),
                    AggregateType.PROBE,
                    aggregate_id,
                )
            )
        return deliveries

    async def _drain_and_check(self) -> None:
        # Faults off, clock far ahead: everything due must now complete.
        later = self.now + timedelta(days=1)
        for _ in range(1000):
            candidates = await find_candidates(self.worker, later, 50)
            if not candidates:
                break
            for candidate in candidates:
                result = await run_one(self.worker, NO_HTTP, ENV, candidate, later)
                assert not isinstance(result, Failed), result
        states = await self._states()
        for state in states.values():
            assert state == ProbeState.FINISHED
        echo_counts = await self._echo_counts()
        for probe_id in states:
            assert echo_counts.get(probe_id) == 1
        cur = await self.worker.execute(
            "SELECT count(*) FROM outbox_delivery"
            " WHERE handled_at IS NULL OR last_error IS NOT NULL"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 0
        assert await self._advisory_locks() == 0


def _examples() -> int:
    # Each example is up to 50 database steps: a few dozen in the normal
    # run, the soak profile's full budget otherwise.
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 100
    return settings().max_examples


def test_probe_flow_survives_any_order_of_steps_crashes_and_repeats(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        ProbeFlow,
        settings=settings(max_examples=_examples(), deadline=None),
    )
