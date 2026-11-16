from datetime import datetime
from typing import assert_never

from mor.db.connect import Connection
from mor.messaging.facts import Fact
from mor.messaging.outcome import ConsumerOutcome
from mor.messaging.routing import ConsumerName
from mor.probe import flow


async def handle(
    conn: Connection, consumer: ConsumerName, fact: Fact, now: datetime
) -> ConsumerOutcome:
    """The one dispatch table: each consumer is a plain function that runs
    inside the worker's transaction and never commits."""
    match consumer:
        case ConsumerName.PROBE_ECHO:
            return await flow.echo(conn, fact, now)
        case ConsumerName.PROBE_FINISH:
            return await flow.finish(conn, fact, now)
        case _:
            assert_never(consumer)
