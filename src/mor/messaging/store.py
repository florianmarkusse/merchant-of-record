import uuid
from dataclasses import dataclass
from datetime import datetime

from mor.audit import Actor
from mor.db.connect import Connection
from mor.db.fetch import one_value
from mor.messaging.facts import Fact, aggregate_of, encode, fact_type
from mor.messaging.routing import ConsumerName, consumers_for


@dataclass(frozen=True)
class Handled:
    pass


@dataclass(frozen=True)
class AlreadyHandled:
    pass


async def append_fact(
    conn: Connection,
    fact: Fact,
    actor: Actor,
    now: datetime,
    deliver_at: datetime | None = None,
) -> uuid.UUID:
    """Write a fact and a delivery for each of its consumers, in the caller's
    transaction next to the state change it records. Never commits.
    `deliver_at` schedules the deliveries for later (a poll's next turn):
    time only starts the consumer, which decides under its locks."""
    message_id = uuid.uuid7()
    aggregate = aggregate_of(fact)
    kind = fact_type(fact)
    seq = await one_value(
        conn,
        """
        INSERT INTO outbox
          (id, aggregate_type, aggregate_id, message_type, schema_version,
           payload, created_at, actor_kind, actor_id)
        VALUES
          (%(id)s, %(aggregate_type)s, %(aggregate_id)s, %(message_type)s,
           %(schema_version)s, %(payload)s::jsonb, %(created_at)s,
           %(actor_kind)s, %(actor_id)s)
        RETURNING seq
        """,
        {
            "id": message_id,
            "aggregate_type": aggregate.type.value,
            "aggregate_id": aggregate.id,
            "message_type": kind.message_type.value,
            "schema_version": kind.schema_version,
            "payload": encode(fact).decode("utf-8"),
            "created_at": now,
            "actor_kind": actor.kind.value,
            "actor_id": actor.id,
        },
        int,
    )

    for consumer in consumers_for(kind.message_type):
        await conn.execute(
            """
            INSERT INTO outbox_delivery
              (message_id, consumer, aggregate_type, aggregate_id, seq, not_before)
            VALUES
              (%(message_id)s, %(consumer)s, %(aggregate_type)s, %(aggregate_id)s,
               %(seq)s, %(not_before)s)
            """,
            {
                "message_id": message_id,
                "consumer": consumer.value,
                "aggregate_type": aggregate.type.value,
                "aggregate_id": aggregate.id,
                "seq": seq,
                "not_before": now if deliver_at is None else deliver_at,
            },
        )
    return message_id


async def mark_handled(
    conn: Connection, consumer: ConsumerName, message_id: uuid.UUID, now: datetime
) -> Handled | AlreadyHandled:
    """Record in the inbox that a consumer handled a message, in the
    transaction of its effect. The primary key decides: insert, and a
    conflict means it was handled before. Never commits."""
    cur = await conn.execute(
        """
        INSERT INTO inbox (consumer, message_id, handled_at)
        VALUES (%(consumer)s, %(message_id)s, %(now)s)
        ON CONFLICT (consumer, message_id) DO NOTHING
        RETURNING message_id
        """,
        {"consumer": consumer.value, "message_id": message_id, "now": now},
    )
    if await cur.fetchone() is None:
        return AlreadyHandled()
    return Handled()
