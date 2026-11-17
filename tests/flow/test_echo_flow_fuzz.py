"""Layer 2 for effect consumers: the echo flow as a Hypothesis state machine
against mor_test and the fake echo vendor in-process (plan-messaging §10.6).

Actions: create a job on any provider; run a delivery; crash after
`prepare` committed; crash after the call, before `complete`; script a
fault for the next send; refuse the next connection; redeliver a handled
message; move the clock. After every action the vendor never holds two
echoes for one job on `keyed` or `plain`, and every job is in a state its
provider allows. After draining with faults off, every job is finished and
on `cancellable` an echoed job has exactly one live echo.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta

import httpx2
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
from mor.echo.flow import EchoJobState, create_echo_account, create_echo_job
from mor.echo.port import EchoProvider
from mor.fakes.echo import make_app
from mor.messaging import consumers
from mor.messaging.facts import AggregateType
from mor.messaging.routing import ConsumerName
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
from tests.helpers import ENV, TEST_URL, RefusingTransport, empty_tables


T0 = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
FLAVOUR = {
    EchoProvider.FAKE_KEYED: "keyed",
    EchoProvider.FAKE_CANCELLABLE: "cancellable",
    EchoProvider.FAKE_PLAIN: "plain",
}
FAULTS = ["fail_500", "fail_500_after_work", "timeout_after_work", "refuse"]
FINAL = {EchoJobState.ECHOED, EchoJobState.REFUSED, EchoJobState.NEEDS_PERSON}


class EchoFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        app = make_app(TEST_URL)
        self.network = RefusingTransport(httpx2.ASGITransport(app=app))
        self.http = httpx2.AsyncClient(transport=self.network)
        # Test control (scripting faults) bypasses the refusing network.
        self.control = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.now = T0
        self.accounts: dict[EchoProvider, uuid.UUID] = {}
        for provider in EchoProvider:
            self.accounts[provider] = self.run(self._account(provider))
        self.jobs: dict[uuid.UUID, EchoProvider] = {}
        # The model of when an answer may be lost (so `needs_person` is
        # allowed): a crash on that job, or a fault that loses answers
        # scripted for `plain`. A refused connection never loses one.
        self.crashed_jobs: set[uuid.UUID] = set()
        self.plain_may_lose_answers = False

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

    @rule(provider=st.sampled_from(EchoProvider))
    def create_job(self, provider: EchoProvider) -> None:
        async def step() -> uuid.UUID:
            async with await connect(TEST_URL) as conn:
                job_id = await create_echo_job(
                    conn, self.accounts[provider], "hello", ACTOR, self.now
                )
                await conn.commit()
            return job_id

        self.jobs[self.run(step())] = provider

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule(data=st.data(), with_call=st.booleans())
    def crash_between_commits(self, data: st.DataObject, with_call: bool) -> None:
        """Commit `prepare`, maybe make the call, then die before
        `complete`: a crash at each of the two commit boundaries. Marks the
        job as one whose answer may be lost."""
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        self.crashed_jobs.add(candidate.aggregate_id)

        async def step() -> None:
            crashing = await connect(TEST_URL, autocommit=True)
            assert await try_lock_aggregate(crashing, candidate)
            async with crashing.transaction():
                prepared = await prepare_delivery(crashing, candidate, self.now)
            if with_call and isinstance(prepared, Prepared):
                await consumers.call(self.http, ENV, prepared.call)
            await crashing.close()

        self.run(step())

    @rule(provider=st.sampled_from(EchoProvider), fault=st.sampled_from(FAULTS))
    def script_a_fault(self, provider: EchoProvider, fault: str) -> None:
        if provider == EchoProvider.FAKE_PLAIN and fault != "refuse":
            self.plain_may_lose_answers = True

        async def step() -> None:
            response = await self.control.post(
                "http://fake/_fake/echo/faults",
                json={"flavour": FLAVOUR[provider], "fault": fault},
            )
            assert response.status_code == 204

        self.run(step())

    @rule()
    def refuse_the_next_connection(self) -> None:
        self.network.refuse_next += 1

    @precondition(lambda self: len(self.jobs) > 0)
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
    def keyed_and_plain_never_hold_two_echoes_for_a_job(self) -> None:
        counts = self.run(self._echo_counts(live_only=False))
        for job_id, provider in self.jobs.items():
            if provider != EchoProvider.FAKE_CANCELLABLE:
                assert counts.get(job_id, 0) <= 1, (provider, job_id)

    @invariant()
    def every_job_is_in_a_state_its_provider_allows(self) -> None:
        states = self.run(self._states())
        for job_id, provider in self.jobs.items():
            state = states[job_id]
            if state == EchoJobState.NEEDS_PERSON:
                assert provider == EchoProvider.FAKE_PLAIN
                assert self.plain_may_lose_answers or job_id in self.crashed_jobs
            if state == EchoJobState.DEDUPLICATING:
                assert provider == EchoProvider.FAKE_CANCELLABLE

    @invariant()
    def no_advisory_lock_is_left_held(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
            )
        )
        row = self.run(cur.fetchone())
        assert row is not None and row[0] == 0

    # --- helpers ---------------------------------------------------------

    async def _account(self, provider: EchoProvider) -> uuid.UUID:
        async with await connect(TEST_URL) as conn:
            account_id = await create_echo_account(
                conn,
                provider,
                f"http://fake/_fake/echo/{FLAVOUR[provider]}",
                timedelta(milliseconds=100),
            )
            await conn.commit()
        return account_id

    def _draw_candidate(self, data: st.DataObject) -> Candidate | None:
        candidates = self.run(find_candidates(self.worker, self.now, 50))
        if not candidates:
            return None
        return data.draw(st.sampled_from(candidates))

    async def _handled(self) -> list[Candidate]:
        cur = await self.worker.execute(
            "SELECT message_id, aggregate_id FROM outbox_delivery"
            " WHERE handled_at IS NOT NULL AND consumer = 'echo_send'"
            " ORDER BY seq"
        )
        handled: list[Candidate] = []
        for message_id, job_id in await cur.fetchall():
            handled.append(
                Candidate(
                    message_id, ConsumerName.ECHO_SEND, AggregateType.ECHO_JOB, job_id
                )
            )
        return handled

    async def _states(self) -> dict[uuid.UUID, EchoJobState]:
        cur = await self.worker.execute("SELECT id, state::text FROM echo_job")
        states: dict[uuid.UUID, EchoJobState] = {}
        for job_id, state in await cur.fetchall():
            states[job_id] = EchoJobState(state)
        return states

    async def _echo_counts(self, live_only: bool) -> dict[uuid.UUID, int]:
        cur = await self.worker.execute(
            "SELECT reference, count(*) FROM fake_vendor.echo"
            " WHERE NOT %s OR state = 'live' GROUP BY reference",
            (live_only,),
        )
        counts: dict[uuid.UUID, int] = {}
        for reference, count in await cur.fetchall():
            counts[uuid.UUID(reference)] = count
        return counts

    async def _drain_and_check(self) -> None:
        # Faults off, clock far ahead: everything must finish.
        await self.worker.execute("DELETE FROM fake_vendor.echo_fault")
        self.network.refuse_next = 0
        for _ in range(200):
            self.now += timedelta(hours=2)
            candidates = await find_candidates(self.worker, self.now, 50)
            if not candidates:
                break
            for candidate in candidates:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        states = await self._states()
        live = await self._echo_counts(live_only=True)
        every = await self._echo_counts(live_only=False)
        for job_id, provider in self.jobs.items():
            state = states[job_id]
            assert state in FINAL, (provider, state)
            if state == EchoJobState.ECHOED:
                assert live.get(job_id, 0) == 1, (provider, job_id)
            if state == EchoJobState.REFUSED:
                assert live.get(job_id, 0) == 0, (provider, job_id)
            if provider != EchoProvider.FAKE_CANCELLABLE:
                assert every.get(job_id, 0) <= 1, (provider, job_id)
        cur = await self.worker.execute(
            "SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 0


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 60
    return settings().max_examples


def test_echo_flow_survives_faults_crashes_and_lost_answers_on_every_guard(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        EchoFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
