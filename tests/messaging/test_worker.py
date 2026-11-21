import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection, connect
from mor.messaging.facts import AggregateType, ProbeStartedV1
from mor.messaging.outcome import Applied, Stale
from mor.messaging.routing import ConsumerName
from mor.messaging.store import append_fact
from mor.messaging.worker import (
    Candidate,
    Failed,
    Ran,
    Skipped,
    drain_once,
    find_candidates,
    handle_delivery,
    run_one,
    try_lock_aggregate,
)
from mor.probe.flow import ProbeState, probe_state, start_probe
from tests.helpers import ENV, NO_HTTP, advisory_locks_held, crash, local_consumer


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
CLI = Actor(ActorKind.SYSTEM, "test")


async def _start(url: str) -> uuid.UUID:
    async with await connect(url) as conn:
        probe_id = await start_probe(conn, CLI, NOW)
        await conn.commit()
    return probe_id


async def _state(url: str, probe_id: uuid.UUID) -> ProbeState | None:
    async with await connect(url) as conn:
        return await probe_state(conn, probe_id)


async def _count(url: str, query: bytes) -> int:
    async with await connect(url) as conn:
        cur = await conn.execute(query)
        row = await cur.fetchone()
    assert row is not None
    count = row[0]
    assert isinstance(count, int)
    return count


def _echo_candidate(message_id: uuid.UUID, probe_id: uuid.UUID) -> Candidate:
    return Candidate(message_id, ConsumerName.PROBE_ECHO, AggregateType.PROBE, probe_id)


async def _only_candidate(conn: Connection, now: datetime) -> Candidate:
    candidates = await find_candidates(conn, now, 10)
    assert len(candidates) == 1
    return candidates[0]


def test_worker_takes_a_probe_through_both_steps(database_url: str) -> None:
    async def scenario() -> None:
        probe_id = await _start(database_url)
        async with await connect(database_url, autocommit=True) as worker:
            first = await drain_once(worker, NO_HTTP, ENV, NOW)
            assert first == [Ran(Applied())]
            assert await _state(database_url, probe_id) == ProbeState.ECHOED
            second = await drain_once(worker, NO_HTTP, ENV, NOW)
            assert second == [Ran(Applied())]
            assert await drain_once(worker, NO_HTTP, ENV, NOW) == []
        assert await _state(database_url, probe_id) == ProbeState.FINISHED
        assert await _count(database_url, b"SELECT count(*) FROM outbox") == 2
        assert await _count(database_url, b"SELECT count(*) FROM inbox") == 2
        assert await advisory_locks_held(database_url) == 0

    asyncio.run(scenario())


def test_a_handled_delivery_is_not_run_again(database_url: str) -> None:
    async def scenario() -> None:
        await _start(database_url)
        async with await connect(database_url, autocommit=True) as worker:
            candidate = await _only_candidate(worker, NOW)
            assert await run_one(worker, NO_HTTP, ENV, candidate, NOW) == Ran(Applied())
            # Picked before the run finished, as a second worker would have.
            assert await run_one(worker, NO_HTTP, ENV, candidate, NOW) == Skipped(
                "already_handled"
            )
        count = await _count(database_url, b"SELECT count(*) FROM outbox")
        assert count == 2

    asyncio.run(scenario())


def test_facts_of_one_aggregate_run_in_order_and_a_repeat_is_stale(
    database_url: str,
) -> None:
    async def scenario() -> None:
        probe_id = await _start(database_url)
        # The same fact appended again, as a producer retrying would.
        async with await connect(database_url) as conn:
            repeat_id = await append_fact(
                conn, ProbeStartedV1(probe_id=probe_id), CLI, NOW
            )
            await conn.commit()
        repeat = _echo_candidate(repeat_id, probe_id)
        async with await connect(database_url, autocommit=True) as worker:
            assert await run_one(worker, NO_HTTP, ENV, repeat, NOW) == Skipped(
                "not_next"
            )
            first = await _only_candidate(worker, NOW)
            assert first.message_id != repeat_id
            assert await run_one(worker, NO_HTTP, ENV, first, NOW) == Ran(Applied())
            assert await run_one(worker, NO_HTTP, ENV, repeat, NOW) == Ran(Stale())
        echoed = b"SELECT count(*) FROM outbox WHERE message_type = 'probe_echoed'"
        assert await _count(database_url, echoed) == 1

    asyncio.run(scenario())


def test_two_aggregates_proceed_independently(database_url: str) -> None:
    async def scenario() -> None:
        first = await _start(database_url)
        second = await _start(database_url)
        async with await connect(database_url, autocommit=True) as worker:
            results = await drain_once(worker, NO_HTTP, ENV, NOW)
        assert results == [Ran(Applied()), Ran(Applied())]
        assert await _state(database_url, first) == ProbeState.ECHOED
        assert await _state(database_url, second) == ProbeState.ECHOED

    asyncio.run(scenario())


def test_a_failing_consumer_is_retried_later_and_holds_only_its_aggregate(
    database_url: str,
) -> None:
    async def scenario() -> None:
        broken = await _start(database_url)
        healthy = await _start(database_url)
        # A real fault, not a patched function: the probe's row is gone.
        async with await connect(database_url) as conn:
            await conn.execute("DELETE FROM probe WHERE id = %s", (broken,))
            await conn.commit()
        async with await connect(database_url, autocommit=True) as worker:
            results = await drain_once(worker, NO_HTTP, ENV, NOW)
            assert len(results) == 2
            failed = results[0]
            assert isinstance(failed, Failed)
            assert "does not exist" in failed.error
            assert failed.not_before == NOW + timedelta(seconds=2)
            assert results[1] == Ran(Applied())
            assert await _state(database_url, healthy) == ProbeState.ECHOED

            # The row is put back; before not_before nothing is due for it.
            async with await connect(database_url) as conn:
                await conn.execute(
                    "INSERT INTO probe (id, state, created_at)"
                    " VALUES (%s, 'started', %s)",
                    (broken, NOW),
                )
                await conn.commit()
            soon = NOW + timedelta(seconds=1)
            pending = await find_candidates(worker, soon, 10)
            assert broken not in [c.aggregate_id for c in pending]
            later = NOW + timedelta(seconds=3)
            # The broken probe's echo, on its retry, and the healthy finish.
            assert await drain_once(worker, NO_HTTP, ENV, later) == [
                Ran(Applied()),
                Ran(Applied()),
            ]
        assert await _state(database_url, broken) == ProbeState.ECHOED

    asyncio.run(scenario())


def test_a_worker_skips_an_aggregate_another_holds_until_it_dies(
    database_url: str,
) -> None:
    async def scenario() -> None:
        probe_id = await _start(database_url)
        async with await connect(database_url, autocommit=True) as worker:
            candidate = await _only_candidate(worker, NOW)
            other = await connect(database_url, autocommit=True)
            assert await try_lock_aggregate(other, candidate)
            assert await run_one(worker, NO_HTTP, ENV, candidate, NOW) == Skipped(
                "busy"
            )
            # The holder's connection dies: no timeout, the lock is gone.
            await crash(other)
            assert await run_one(worker, NO_HTTP, ENV, candidate, NOW) == Ran(Applied())
        assert await _state(database_url, probe_id) == ProbeState.ECHOED

    asyncio.run(scenario())


def test_a_crash_before_commit_leaves_nothing_and_frees_the_lock(
    database_url: str,
) -> None:
    async def scenario() -> None:
        probe_id = await _start(database_url)
        crashing = await connect(database_url, autocommit=True)
        candidate = await _only_candidate(crashing, NOW)
        assert await try_lock_aggregate(crashing, candidate)
        await crashing.execute("BEGIN")
        assert await handle_delivery(
            crashing, candidate, local_consumer(candidate), ENV, NOW
        ) == Ran(Applied())
        await crash(crashing)  # dies before COMMIT

        assert await _state(database_url, probe_id) == ProbeState.STARTED
        assert await advisory_locks_held(database_url) == 0
        async with await connect(database_url, autocommit=True) as worker:
            assert await run_one(worker, NO_HTTP, ENV, candidate, NOW) == Ran(Applied())
        echoed = b"SELECT count(*) FROM outbox WHERE message_type = 'probe_echoed'"
        assert await _count(database_url, echoed) == 1

    asyncio.run(scenario())


def test_database_refuses_reopening_a_handled_delivery_or_editing_a_fact(
    database_url: str,
) -> None:
    async def scenario() -> None:
        await _start(database_url)
        async with await connect(database_url, autocommit=True) as worker:
            await drain_once(worker, NO_HTTP, ENV, NOW)
        for statement, message in [
            (b"UPDATE outbox_delivery SET handled_at = NULL", "already handled"),
            (b"DELETE FROM outbox_delivery", "never deleted"),
            (b"UPDATE outbox SET payload = '{}'", "append-only"),
            (b"DELETE FROM inbox", "append-only"),
        ]:
            async with await connect(database_url) as conn:
                with pytest.raises(psycopg.errors.RaiseException, match=message):
                    await conn.execute(statement)

    asyncio.run(scenario())
