"""The probe: a two-step flow through two local consumers.

    start_probe            → started   + probe_started
    probe_echo   on probe_started → echoed    + probe_echoed
    probe_finish on probe_echoed  → finished

It proves the worker runs and reaches the database (`python -m mor.probe`
after a deploy), and it is the flow the messaging fuzzer drives.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.db.fetch import one_row
from mor.messaging.facts import ProbeEchoedV1, ProbeStartedV1
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.store import append_fact
from mor.probe.records import ProbeState


def after_echo(state: ProbeState) -> ProbeState | Stale:
    match state:
        case ProbeState.STARTED:
            return ProbeState.ECHOED
        case ProbeState.ECHOED | ProbeState.FINISHED:
            return Stale()


def after_finish(state: ProbeState) -> ProbeState | Stale:
    match state:
        case ProbeState.STARTED:
            # probe_echoed is written only by the step to echoed.
            raise AssertionError("probe_echoed for a probe still started")
        case ProbeState.ECHOED:
            return ProbeState.FINISHED
        case ProbeState.FINISHED:
            return Stale()


async def start_probe(conn: Connection, actor: Actor, now: datetime) -> uuid.UUID:
    probe_id = uuid.uuid7()
    await conn.execute(
        "INSERT INTO probe (id, state, created_at) VALUES (%s, 'started', %s)",
        (probe_id, now),
    )
    await append_fact(conn, ProbeStartedV1(probe_id=probe_id), actor, now)
    return probe_id


async def probe_state(conn: Connection, probe_id: uuid.UUID) -> ProbeState | None:
    cur = await conn.execute("SELECT state FROM probe WHERE id = %s", (probe_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    return ProbeState(row[0])


@dataclass(frozen=True)
class LockedProbe:
    """A probe's row, read under its row lock."""

    id: uuid.UUID
    state: ProbeState


async def lock_probe(conn: Connection, probe_id: uuid.UUID) -> LockedProbe:
    async with conn.cursor(row_factory=class_row(LockedProbe)) as cur:
        await cur.execute(
            "SELECT id, state FROM probe WHERE id = %s FOR UPDATE", (probe_id,)
        )
        return await one_row(cur, f"probe {probe_id}")


async def echo(conn: Connection, probe: LockedProbe, now: datetime) -> ConsumerOutcome:
    """Consumer `probe_echo`, on probe_started, the probe locked."""
    probe_id = probe.id
    match after_echo(probe.state):
        case Stale() as stale:
            return stale
        case new_state:
            await conn.execute(
                "UPDATE probe SET state = %s, echoed_at = %s WHERE id = %s",
                (new_state.value, now, probe_id),
            )
            await append_fact(
                conn,
                ProbeEchoedV1(probe_id=probe_id),
                Actor(ActorKind.SYSTEM, "probe_echo"),
                now,
            )
            return Applied()


async def finish(
    conn: Connection, probe: LockedProbe, now: datetime
) -> ConsumerOutcome:
    """Consumer `probe_finish`, on probe_echoed, the probe locked."""
    probe_id = probe.id
    match after_finish(probe.state):
        case Stale() as stale:
            return stale
        case new_state:
            await conn.execute(
                "UPDATE probe SET state = %s, finished_at = %s WHERE id = %s",
                (new_state.value, now, probe_id),
            )
            return Applied()
