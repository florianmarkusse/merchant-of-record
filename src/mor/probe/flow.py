"""The probe: a two-step flow through two local consumers.

    start_probe            → started   + probe_started
    probe_echo   on probe_started → echoed    + probe_echoed
    probe_finish on probe_echoed  → finished

It proves the worker runs and reaches the database (`python -m mor.probe`
after a deploy), and it is the flow the messaging fuzzer drives.
"""

import uuid
from datetime import datetime

from mor.audit import Actor, ActorKind
from mor.db.connect import Connection
from mor.messaging.facts import Fact, ProbeEchoedV1, ProbeStartedV1
from mor.messaging.outcome import Applied, ConsumerOutcome, Stale
from mor.messaging.routing import WrongFact
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


async def _lock(conn: Connection, probe_id: uuid.UUID) -> ProbeState:
    cur = await conn.execute(
        "SELECT state FROM probe WHERE id = %s FOR UPDATE", (probe_id,)
    )
    row = await cur.fetchone()
    if row is None:
        raise LookupError(f"probe {probe_id} does not exist")
    return ProbeState(row[0])


async def echo(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `probe_echo`, on probe_started."""
    match fact:
        case ProbeStartedV1(probe_id=probe_id):
            pass
        case _:
            raise WrongFact(f"probe_echo got {fact!r}")
    state = await _lock(conn, probe_id)
    match after_echo(state):
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


async def finish(conn: Connection, fact: Fact, now: datetime) -> ConsumerOutcome:
    """Consumer `probe_finish`, on probe_echoed."""
    match fact:
        case ProbeEchoedV1(probe_id=probe_id):
            pass
        case _:
            raise WrongFact(f"probe_finish got {fact!r}")
    state = await _lock(conn, probe_id)
    match after_finish(state):
        case Stale() as stale:
            return stale
        case new_state:
            await conn.execute(
                "UPDATE probe SET state = %s, finished_at = %s WHERE id = %s",
                (new_state.value, now, probe_id),
            )
            return Applied()
