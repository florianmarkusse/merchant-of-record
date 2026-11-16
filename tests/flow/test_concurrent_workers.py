"""Layer 3 (plan-architecture.md §10): real workers on their own connections
against the same rows, at once. Not shrunk; it checks that the locks do
what the serial fuzzer assumes."""

import asyncio
from datetime import UTC, datetime

from mor.audit import Actor, ActorKind
from mor.db.connect import connect
from mor.messaging.worker import Failed, Ran, RunResult, Skipped, drain_once
from mor.probe.flow import start_probe
from tests.helpers import advisory_locks_held


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
PROBES = 200
WORKERS = 6


async def _worker(url: str, results: list[RunResult]) -> None:
    async with await connect(url, autocommit=True) as conn:
        idle_rounds = 0
        while idle_rounds < 3:
            round_results = await drain_once(conn, NOW, limit=20)
            results.extend(round_results)
            ran = False
            for result in round_results:
                if isinstance(result, Ran):
                    ran = True
            idle_rounds = 0 if ran else idle_rounds + 1
            await asyncio.sleep(0)


async def _count(url: str, query: bytes) -> int:
    async with await connect(url) as conn:
        cur = await conn.execute(query)
        row = await cur.fetchone()
    assert row is not None
    count = row[0]
    assert isinstance(count, int)
    return count


def test_many_workers_finish_every_probe_once(database_url: str) -> None:
    async def scenario() -> list[RunResult]:
        async with await connect(database_url) as conn:
            for _ in range(PROBES):
                await start_probe(conn, Actor(ActorKind.SYSTEM, "test"), NOW)
            await conn.commit()
        results: list[RunResult] = []
        await asyncio.gather(*[_worker(database_url, results) for _ in range(WORKERS)])
        return results

    results = asyncio.run(scenario())

    ran = 0
    for result in results:
        assert not isinstance(result, Failed), result
        if isinstance(result, Ran):
            ran += 1
        else:
            assert isinstance(result, Skipped)
    # Each probe: one echo and one finish ran, whatever the interleaving.
    assert ran == 2 * PROBES

    async def checks() -> None:
        finished = b"SELECT count(*) FROM probe WHERE state = 'finished'"
        assert await _count(database_url, finished) == PROBES
        echoed = b"SELECT count(*) FROM outbox WHERE message_type = 'probe_echoed'"
        assert await _count(database_url, echoed) == PROBES
        inbox = b"SELECT count(*) FROM inbox"
        assert await _count(database_url, inbox) == 2 * PROBES
        open_deliveries = (
            b"SELECT count(*) FROM outbox_delivery WHERE handled_at IS NULL"
        )
        assert await _count(database_url, open_deliveries) == 0
        assert await advisory_locks_held(database_url) == 0

    asyncio.run(checks())
