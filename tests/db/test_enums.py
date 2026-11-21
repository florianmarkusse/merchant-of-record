"""Database enums come back as our StrEnums, and each listed one has
exactly the labels of its StrEnum: a value added on one side only fails
here, not on the first row that carries it."""

import asyncio
from typing import get_args

from mor.db.connect import connect
from mor.db.enums import DATABASE_ENUMS
from mor.merchants.records import KybState
from mor.quote.shipping import NotOfferedReason


def test_every_listed_enum_has_the_labels_of_its_strenum(database_url: str) -> None:
    async def body() -> dict[str, list[str]]:
        async with await connect(database_url) as conn:
            cur = await conn.execute(
                """
                SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE n.nspname = 'public'
                GROUP BY t.typname
                """
            )
            labels: dict[str, list[str]] = {}
            for name, values in await cur.fetchall():
                labels[name] = values
            return labels

    labels = asyncio.run(body())
    for name, enum in DATABASE_ENUMS:
        assert name in labels, f"{name} is not a database enum"
        values: list[str] = []
        for member in enum:
            values.append(member.value)
        assert sorted(labels[name]) == sorted(values), name
    # Read as text into a Literal rather than loaded as a StrEnum.
    reasons: list[str] = []
    for reason in get_args(NotOfferedReason.__value__):
        reasons.append(reason)
    assert sorted(labels["not_offered_reason"]) == sorted(reasons)


def test_an_enum_column_reads_back_as_its_member(database_url: str) -> None:
    async def body() -> tuple[object, ...] | None:
        async with await connect(database_url) as conn:
            cur = await conn.execute(
                "SELECT %s::merchant_kyb_state, %s::merchant_kyb_state[]",
                (KybState.APPROVED, [KybState.PENDING]),
            )
            return await cur.fetchone()

    row = asyncio.run(body())
    assert row == (KybState.APPROVED, [KybState.PENDING])
    assert row is not None
    assert type(row[0]) is KybState
