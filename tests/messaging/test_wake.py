"""Idle workers wake on NOTIFY, on the next retry, or on the fallback poll."""

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta

from mor.audit import Actor, ActorKind
from mor.db.connect import connect
from mor.messaging.facts import ProbeStartedV1
from mor.messaging.store import append_fact
from mor.messaging.worker import (
    Failed,
    drain_once,
    idle_wait,
    listen,
    next_retry_at,
    run_worker,
    wait_for_work,
)
from mor.probe.flow import ProbeState, probe_state, start_probe


NOW = datetime(2026, 11, 17, 9, 30, tzinfo=UTC)
ACTOR = Actor(ActorKind.SYSTEM, "test")
FALLBACK = timedelta(seconds=5)


def test_idle_wait_is_the_fallback_or_less_until_the_next_retry() -> None:
    assert idle_wait(NOW, None, FALLBACK) == FALLBACK
    assert idle_wait(NOW, NOW + timedelta(seconds=2), FALLBACK) == timedelta(seconds=2)
    assert idle_wait(NOW, NOW + timedelta(minutes=5), FALLBACK) == FALLBACK
    assert idle_wait(NOW, NOW - timedelta(seconds=1), FALLBACK) == timedelta(0)


def test_next_retry_counts_only_deliveries_that_could_run_next(
    database_url: str,
) -> None:
    async def scenario() -> None:
        async with await connect(database_url, autocommit=True) as worker:
            assert await next_retry_at(worker, NOW) is None
            async with await connect(database_url) as conn:
                probe_id = await start_probe(conn, ACTOR, NOW)
                await conn.execute("DELETE FROM probe WHERE id = %s", (probe_id,))
                await conn.commit()
            results = await drain_once(worker, NOW)
            assert len(results) == 1 and isinstance(results[0], Failed)
            # A second fact for the same probe is due now but waits behind
            # the failed one: it must not make the idle wait zero.
            async with await connect(database_url) as conn:
                await append_fact(conn, ProbeStartedV1(probe_id=probe_id), ACTOR, NOW)
                await conn.commit()
            assert await next_retry_at(worker, NOW) == NOW + timedelta(seconds=2)

    asyncio.run(scenario())


def test_a_committed_delivery_wakes_a_waiting_worker(database_url: str) -> None:
    async def scenario() -> float:
        async with await connect(database_url, autocommit=True) as worker:
            await listen(worker)
            waiting = asyncio.create_task(wait_for_work(worker, timedelta(seconds=10)))
            await asyncio.sleep(0.1)
            started = time.monotonic()
            async with await connect(database_url) as conn:
                await start_probe(conn, ACTOR, NOW)
                await conn.commit()
            assert await waiting
            return time.monotonic() - started

    assert asyncio.run(scenario()) < 2


def test_a_rolled_back_delivery_wakes_nobody(database_url: str) -> None:
    async def scenario() -> bool:
        async with await connect(database_url, autocommit=True) as worker:
            await listen(worker)
            async with await connect(database_url) as conn:
                await start_probe(conn, ACTOR, NOW)
                await conn.rollback()
            return await wait_for_work(worker, timedelta(seconds=0.3))

    assert asyncio.run(scenario()) is False


def test_running_worker_finishes_a_new_probe_well_before_its_fallback_poll(
    database_url: str,
) -> None:
    async def scenario() -> ProbeState | None:
        worker = asyncio.create_task(run_worker(database_url))
        await asyncio.sleep(0.3)  # let it drain nothing and start waiting
        async with await connect(database_url) as conn:
            probe_id = await start_probe(conn, ACTOR, datetime.now(UTC))
            await conn.commit()
            state: ProbeState | None = None
            for _ in range(40):  # 2 s, under the 5 s fallback
                state = await probe_state(conn, probe_id)
                await conn.commit()
                if state == ProbeState.FINISHED:
                    break
                await asyncio.sleep(0.05)
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        return state

    assert asyncio.run(scenario()) == ProbeState.FINISHED
