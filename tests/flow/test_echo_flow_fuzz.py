"""Layer 2 for effect consumers: the echo flow as a Hypothesis state machine
against mor_test and the fake echo vendor in-process (plan-messaging §10.6).

Actions: create a job on any provider; run a delivery; the worker
catches up (runs everything due, so "an event fully processed before the
next one arrives" is reachable in a few steps); crash after
`prepare` committed; crash after the call, before `complete`; script a
fault for the next send; refuse the next connection; redeliver a handled
message; move the clock. Inbound (§12): accounts in `push`, `push_thin`,
`poll`, `file` and `none` mode; release the next or a named event,
pushed once, twice or badly signed; redeliver failed webhooks; cut a
report; record an event by hand; fail the next events or report fetch.
While draining, the vendor releases every remaining event and cuts
reports, so polling ends; a `file` account keeps exactly one scheduled
look for reports.

After every action the vendor never holds two echoes for one job on
`keyed` or `plain`, every job is in a state its provider allows, and no
job's vendor status runs ahead of what was released or recorded. After
draining with faults off, every job is finished, on `cancellable` an
echoed job has exactly one live echo, and every job's vendor status is
`derive_status` of what reached it (released on a push account, or
recorded by hand), with one milestone for it.
"""

import asyncio
import os
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import LiteralString

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
from mor.echo.events import VendorStatus, derive_status, rank
from mor.echo.flow import create_echo_account, create_echo_job
from mor.echo.inbound import record_manual_event
from mor.echo.port import EchoProvider, EventKind, EventMode
from mor.echo.records import EchoJobState
from mor.echo.reports import start_report_schedule
from mor.messaging import consumers
from mor.messaging.facts import AggregateType
from mor.messaging.routing import ConsumerName, Effect, Local, consumer_kind
from mor.messaging.worker import (
    Candidate,
    Failed,
    Prepared,
    Skipped,
    find_candidates,
    handle_delivery,
    prepare_delivery,
    run_one,
    try_lock_aggregate,
)
from tests.helpers import (
    ENV,
    TEST_URL,
    VENDOR_TIMEOUT,
    crash,
    empty_tables,
    wire,
)


T0 = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "fuzz")
FLAVOUR = {
    EchoProvider.FAKE_KEYED: "keyed",
    EchoProvider.FAKE_CANCELLABLE: "cancellable",
    EchoProvider.FAKE_PLAIN: "plain",
}
FAULTS = ["fail_500", "fail_500_after_work", "timeout_after_work", "refuse"]
FINAL = {EchoJobState.ECHOED, EchoJobState.REFUSED, EchoJobState.NEEDS_PERSON}
MODES = [
    EventMode.PUSH,
    EventMode.PUSH_THIN,
    EventMode.POLL,
    EventMode.FILE,
    EventMode.NONE,
]
LISTENING = {EventMode.PUSH, EventMode.PUSH_THIN, EventMode.POLL, EventMode.FILE}
PUSHED = {EventMode.PUSH, EventMode.PUSH_THIN}
PERSON = Actor(ActorKind.STAFF, "fuzz-person")
SECRET = "fuzz-secret"


class EchoFlow(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.run(empty_tables(TEST_URL))
        self.now = T0
        wired = wire(TEST_URL, lambda: self.now)
        self.network = wired.network
        self.http = wired.http
        # Test control (faults, releases) bypasses the refusing network.
        self.control = wired.control
        self.worker: Connection = self.run(connect(TEST_URL, autocommit=True))
        self.accounts: dict[tuple[EchoProvider, EventMode], uuid.UUID] = {}
        self.base_urls: dict[uuid.UUID, str] = {}
        for provider in EchoProvider:
            for mode in MODES:
                account_id = self.run(self._account(provider, mode))
                self.accounts[(provider, mode)] = account_id
        self.jobs: dict[uuid.UUID, EchoProvider] = {}
        self.job_mode: dict[uuid.UUID, EventMode] = {}
        self.job_account: dict[uuid.UUID, uuid.UUID] = {}
        self.manual: dict[uuid.UUID, set[EventKind]] = {}
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

    @rule(provider=st.sampled_from(EchoProvider), mode=st.sampled_from(MODES))
    def create_job(self, provider: EchoProvider, mode: EventMode) -> None:
        account_id = self.accounts[(provider, mode)]

        async def step() -> uuid.UUID:
            async with await connect(TEST_URL) as conn:
                job_id = await create_echo_job(
                    conn, account_id, "hello", ACTOR, self.now
                )
                await conn.commit()
            return job_id

        job_id = self.run(step())
        self.jobs[job_id] = provider
        self.job_mode[job_id] = mode
        self.job_account[job_id] = account_id
        self.manual[job_id] = set()

    @rule(
        data=st.data(),
        kind=st.sampled_from([None, EventKind.CONFIRMED, EventKind.ARCHIVED]),
        push=st.sampled_from(["once", "twice", "bad_signature"]),
    )
    def vendor_releases_an_event(
        self, data: st.DataObject, kind: EventKind | None, push: str
    ) -> None:
        echoed = self.run(self._vendor_echo_ids())
        if not echoed:
            return
        job_id = data.draw(st.sampled_from(sorted(echoed)))
        body: dict[str, str] = {"push": push}
        if kind is not None:
            body["kind"] = kind.value
        url = f"{self.base_urls[self.job_account[job_id]]}/echoes/{echoed[job_id]}"
        response = self.run(self.control.post(f"{url}/events/release", json=body))
        assert response.status_code in (200, 409), response.text

    @rule(provider=st.sampled_from(EchoProvider))
    def vendor_cuts_a_report(self, provider: EchoProvider) -> None:
        account_id = self.accounts[(provider, EventMode.FILE)]
        url = f"{self.base_urls[account_id]}/reports/cut"
        response = self.run(self.control.post(url))
        assert response.status_code == 200

    @rule(data=st.data())
    def vendor_redelivers_failed_webhooks(self, data: st.DataObject) -> None:
        provider = data.draw(st.sampled_from(EchoProvider))
        mode = data.draw(st.sampled_from(sorted(PUSHED)))
        account_id = self.accounts[(provider, mode)]
        url = f"{self.base_urls[account_id]}/events/redeliver"
        response = self.run(self.control.post(url))
        assert response.status_code == 200

    @precondition(lambda self: len(self.jobs) > 0)
    @rule(data=st.data(), kind=st.sampled_from(EventKind))
    def a_person_records_an_event(self, data: st.DataObject, kind: EventKind) -> None:
        job_id = data.draw(st.sampled_from(sorted(self.jobs)))

        async def step() -> None:
            async with await connect(TEST_URL) as conn:
                recorded = await record_manual_event(
                    conn, job_id, kind, self.now, PERSON, self.now
                )
                await conn.commit()
            assert isinstance(recorded, uuid.UUID)

        self.run(step())
        self.manual[job_id].add(kind)

    @rule(data=st.data())
    def run_a_delivery(self, data: st.DataObject) -> None:
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        result = self.run(run_one(self.worker, self.http, ENV, candidate, self.now))
        assert not isinstance(result, Failed), result

    @rule()
    def the_worker_catches_up(self) -> None:
        """Everything due, until nothing is: what a running worker does
        between two events. Without it, a random pick of single deliveries
        seldom finishes one event before the next (a thin-webhook bug hid
        that way, 17-Nov-2026)."""
        self.run(self._catch_up())

    @rule(data=st.data(), with_call=st.booleans())
    def crash_between_commits(self, data: st.DataObject, with_call: bool) -> None:
        """A crash at each commit boundary. Effect consumers: commit
        `prepare`, maybe make the call, die before `complete`. Local ones:
        run the step, die before COMMIT. Marks the aggregate as one whose
        answer may be lost."""
        candidate = self._draw_candidate(data)
        if candidate is None:
            return
        self.crashed_jobs.add(candidate.aggregate_id)

        async def step() -> None:
            crashing = await connect(TEST_URL, autocommit=True)
            try:
                match consumer_kind(candidate.consumer):
                    case Local(consumer=local):
                        # One transaction: run it, die before COMMIT.
                        await crashing.execute("BEGIN")
                        await handle_delivery(crashing, candidate, local, ENV, self.now)
                    case Effect(consumer=effect):
                        assert await try_lock_aggregate(crashing, candidate)
                        async with crashing.transaction():
                            prepared = await prepare_delivery(
                                crashing, candidate, effect, self.now
                            )
                        if with_call and isinstance(prepared, Prepared):
                            await consumers.call(self.http, ENV, prepared.call)
            finally:
                await crash(crashing)

        self.run(step())

    @rule(
        provider=st.sampled_from(EchoProvider),
        fault=st.sampled_from(FAULTS),
        operation=st.sampled_from(["send", "events"]),
    )
    def script_a_fault(
        self, provider: EchoProvider, fault: str, operation: str
    ) -> None:
        if provider == EchoProvider.FAKE_PLAIN and fault != "refuse":
            if operation == "send":
                self.plain_may_lose_answers = True

        async def step() -> None:
            response = await self.control.post(
                "http://fake/_fake/echo/faults",
                json={
                    "flavour": FLAVOUR[provider],
                    "fault": fault,
                    "operation": operation,
                },
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
    def no_vendor_status_runs_ahead_of_its_events(self) -> None:
        statuses = self.run(self._vendor_statuses())
        reachable = self.run(self._reachable_kinds())
        for job_id in self.jobs:
            assert rank(statuses[job_id]) <= rank(derive_status(reachable[job_id]))

    @invariant()
    def each_milestone_fact_is_emitted_at_most_once(self) -> None:
        cur = self.run(
            self.worker.execute(
                "SELECT count(*) FROM outbox"
                " WHERE message_type IN ('echo_job_confirmed', 'echo_job_archived')"
                " GROUP BY aggregate_id, message_type HAVING count(*) > 1"
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
        row = self.run(cur.fetchone())
        assert row is not None and row[0] == 0

    # --- helpers ---------------------------------------------------------

    async def _account(self, provider: EchoProvider, mode: EventMode) -> uuid.UUID:
        base_url = f"http://fake/_fake/echo/{FLAVOUR[provider]}/acct-{mode.value}"
        async with await connect(TEST_URL) as conn:
            account_id = await create_echo_account(
                conn,
                provider,
                base_url,
                VENDOR_TIMEOUT,
                event_mode=mode,
                webhook_secret=SECRET,
                poll_interval=timedelta(minutes=1),
            )
            await conn.commit()
        self.base_urls[account_id] = base_url
        if mode == EventMode.FILE:
            async with await connect(TEST_URL) as conn:
                await start_report_schedule(conn, account_id, ACTOR, self.now)
                await conn.commit()
        if mode in PUSHED:
            response = await self.control.post(
                f"{base_url}/webhooks",
                json={
                    "url": f"http://mor/webhooks/echo/{account_id}",
                    "secret": SECRET,
                    "payload": "full" if mode == EventMode.PUSH else "thin",
                },
            )
            assert response.status_code == 204
        return account_id

    async def _vendor_releases_everything_left(self) -> None:
        """While draining: the vendor releases every event not released yet
        (so every listening job can reach its final status and polling
        ends), and pushes again what failed."""
        for job_id, echo_id in (await self._vendor_echo_ids()).items():
            url = f"{self.base_urls[self.job_account[job_id]]}/echoes/{echo_id}"
            for kind in EventKind:
                await self.control.post(
                    f"{url}/events/release", json={"kind": kind.value}
                )
        for provider in EchoProvider:
            for mode in PUSHED:
                account_id = self.accounts[(provider, mode)]
                await self.control.post(
                    f"{self.base_urls[account_id]}/events/redeliver"
                )
            account_id = self.accounts[(provider, EventMode.FILE)]
            await self.control.post(f"{self.base_urls[account_id]}/reports/cut")

    async def _evidence(self, job_id: uuid.UUID) -> str:
        """What the vendor released and pushed for a job, what reached us,
        and what the job recorded: enough to explain a wrong status from a
        failure message alone (one was seen once and not reproduced,
        17-Nov-2026; plan-messaging §13)."""
        parts = [f"mode {self.job_mode[job_id]}, provider {self.jobs[job_id]}"]
        queries: list[tuple[str, LiteralString]] = [
            (
                "vendor",
                "SELECT e.id, e.state::text, v.kind::text, v.push::text, v.report"
                " FROM fake_vendor.echo e LEFT JOIN fake_vendor.echo_event v"
                " ON v.echo_id = e.id WHERE e.reference = %s",
            ),
            (
                "recorded",
                "SELECT vendor_event_id, kind::text, source::text, inbox_id"
                " FROM echo_job_event WHERE echo_job_id = %s::uuid",
            ),
            (
                "job",
                "SELECT state::text, vendor_status::text, vendor_echo_id"
                " FROM echo_job WHERE id = %s::uuid",
            ),
            (
                "inbox",
                "SELECT i.mode::text, convert_from(i.body, 'UTF8') FROM echo_inbox i"
                " JOIN echo_job j ON j.account_id = i.account_id"
                " WHERE j.id = %s::uuid",
            ),
        ]
        for label, query in queries:
            cur = await self.worker.execute(query, (str(job_id),))
            parts.append(f"{label}: {await cur.fetchall()}")
        parts.append(f"manual: {sorted(self.manual[job_id])}")
        return "; ".join(parts)

    async def _catch_up(self) -> None:
        for _ in range(100):
            candidates = await find_candidates(self.worker, self.now, 50)
            if not candidates:
                return
            for candidate in candidates:
                result = await run_one(self.worker, self.http, ENV, candidate, self.now)
                assert not isinstance(result, Failed), result
        raise AssertionError("the worker never caught up")

    async def _progress(self) -> tuple[object, ...]:
        cur = await self.worker.execute(
            "SELECT (SELECT count(*) FROM echo_inbox),"
            " (SELECT count(*) FROM echo_job_event),"
            " (SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
            "  AND consumer <> 'echo_report_fetch')"
        )
        row = await cur.fetchone()
        assert row is not None
        return tuple(row)

    async def _vendor_echo_ids(self) -> dict[uuid.UUID, str]:
        cur = await self.worker.execute(
            "SELECT id, vendor_echo_id FROM echo_job WHERE vendor_echo_id IS NOT NULL"
        )
        echoed: dict[uuid.UUID, str] = {}
        for job_id, echo_id in await cur.fetchall():
            echoed[job_id] = echo_id
        return echoed

    async def _vendor_statuses(self) -> dict[uuid.UUID, VendorStatus]:
        cur = await self.worker.execute("SELECT id, vendor_status::text FROM echo_job")
        statuses: dict[uuid.UUID, VendorStatus] = {}
        for job_id, status in await cur.fetchall():
            statuses[job_id] = VendorStatus(status)
        return statuses

    async def _reachable_kinds(self) -> dict[uuid.UUID, set[EventKind]]:
        """What may reach each job: events recorded by hand, plus events the
        vendor released, if the job's account listens (push)."""
        reachable: dict[uuid.UUID, set[EventKind]] = {}
        for job_id, kinds in self.manual.items():
            reachable[job_id] = set(kinds)
        cur = await self.worker.execute(
            "SELECT e.reference, v.kind::text FROM fake_vendor.echo_event v"
            " JOIN fake_vendor.echo e ON e.id = v.echo_id"
        )
        for reference, kind in await cur.fetchall():
            job_id = uuid.UUID(reference)
            if self.job_mode[job_id] in LISTENING:
                reachable[job_id].add(EventKind(kind))
        return reachable

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
                    message_id=message_id,
                    consumer=ConsumerName.ECHO_SEND,
                    aggregate_type=AggregateType.ECHO_JOB,
                    aggregate_id=job_id,
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
        # Each round: the vendor releases and cuts, the worker catches up.
        # A `file` account looks for reports forever, and a look reads one
        # report, then looks again at once; catching up runs those looks
        # within the round, so a round that changes nothing ends the drain.
        # (Stopping after one round of looks instead stopped one look short
        # of a report, behind an empty one already stored, 17-Nov-2026.)
        for _ in range(50):
            self.now += timedelta(hours=2)
            await self._vendor_releases_everything_left()
            before = await self._progress()
            await self._catch_up()
            if await self._progress() == before:
                break
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
            "SELECT consumer::text, aggregate_id FROM outbox_delivery"
            " WHERE handled_at IS NULL"
        )
        open_deliveries = await cur.fetchall()
        expected_open: list[tuple[str, uuid.UUID]] = []
        for provider in EchoProvider:
            account_id = self.accounts[(provider, EventMode.FILE)]
            expected_open.append(("echo_report_fetch", account_id))
        assert sorted(open_deliveries) == sorted(expected_open)

        statuses = await self._vendor_statuses()
        reachable = await self._reachable_kinds()
        cur = await self.worker.execute(
            "SELECT echo_job_id, status::text FROM echo_job_milestone"
        )
        milestones: dict[uuid.UUID, set[VendorStatus]] = {}
        for job_id, status in await cur.fetchall():
            milestones.setdefault(job_id, set()).add(VendorStatus(status))
        for job_id in self.jobs:
            expected = derive_status(reachable[job_id])
            if statuses[job_id] != expected:
                evidence = await self._evidence(job_id)
                raise AssertionError(f"{job_id}: expected {expected}; {evidence}")
            if expected != VendorStatus.NONE:
                assert expected in milestones.get(job_id, set())


def _examples() -> int:
    if os.environ.get("HYPOTHESIS_PROFILE", "dev") == "dev":
        return 30
    return settings().max_examples


def test_echo_flow_survives_faults_crashes_and_lost_answers_on_every_guard(
    migrated_test_database: str,
) -> None:
    run_state_machine_as_test(
        EchoFlow, settings=settings(max_examples=_examples(), deadline=None)
    )
