"""The dispatch tables: each consumer is plain functions, picked by an
exhaustive `match` on its name. A local consumer has `handle`; an effect
consumer has `prepare`, `call` and `complete` (plan-architecture.md §5).
Asking a consumer for a step of the other kind is a bug, so it raises."""

from datetime import datetime
from typing import assert_never

import httpx2

from mor.config import Environment
from mor.db.connect import Connection
from mor.echo import flow as echo_flow
from mor.messaging.facts import Fact
from mor.messaging.outcome import ConsumerOutcome, NoCall, StepResult
from mor.messaging.routing import ConsumerName
from mor.probe import flow as probe_flow


type EffectCall = echo_flow.EchoCall
type EffectResult = echo_flow.EchoResult


class WrongKind(Exception):
    """A step was asked of a consumer of the other kind."""


async def handle(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """A local consumer, in the worker's transaction. Never commits."""
    match consumer:
        case ConsumerName.PROBE_ECHO:
            return await probe_flow.echo(conn, fact, now)
        case ConsumerName.PROBE_FINISH:
            return await probe_flow.finish(conn, fact, now)
        case ConsumerName.ECHO_SEND:
            raise WrongKind(f"{consumer} is an effect consumer")
        case _:
            assert_never(consumer)


async def prepare(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
) -> NoCall | EffectCall:
    """An effect consumer's first step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            return await echo_flow.prepare(conn, fact, now)
        case ConsumerName.PROBE_ECHO | ConsumerName.PROBE_FINISH:
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)


async def call(
    http: httpx2.AsyncClient, environment: Environment, effect_call: EffectCall
) -> EffectResult:
    """The outside call, with no transaction open. The call record says
    which port it is for."""
    return await echo_flow.call(http, environment, effect_call)


async def complete(
    conn: Connection,
    consumer: ConsumerName,
    fact: Fact,
    effect_call: EffectCall,
    result: EffectResult,
    now: datetime,
) -> StepResult:
    """An effect consumer's last step, in its own transaction."""
    match consumer:
        case ConsumerName.ECHO_SEND:
            return await echo_flow.complete(conn, fact, effect_call, result, now)
        case ConsumerName.PROBE_ECHO | ConsumerName.PROBE_FINISH:
            raise WrongKind(f"{consumer} is a local consumer")
        case _:
            assert_never(consumer)
