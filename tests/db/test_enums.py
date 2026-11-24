"""Database enums come back as our StrEnums: every one is listed, with
exactly the labels of its StrEnum: a value added on one side only fails
here, not on the first row that carries it."""

import asyncio
from dataclasses import dataclass

from psycopg.rows import class_row

from mor.db.connect import connect
from mor.db.enums import DATABASE_ENUMS
from mor.merchants.records import KybState


@dataclass(frozen=True)
class _EnumLabels:
    typname: str
    labels: list[str]


def test_every_database_enum_has_a_strenum_with_its_labels(database_url: str) -> None:
    async def body() -> dict[str, list[str]]:
        async with await connect(database_url) as conn:
            cur = await conn.cursor(row_factory=class_row(_EnumLabels)).execute(
                """
                SELECT t.typname,
                       array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE n.nspname = 'public'
                GROUP BY t.typname
                """
            )
            labels: dict[str, list[str]] = {}
            for enum_type in await cur.fetchall():
                labels[enum_type.typname] = enum_type.labels
            return labels

    labels = asyncio.run(body())
    listed: set[str] = set()
    for name, enum in DATABASE_ENUMS:
        listed.add(name)
        assert name in labels, f"{name} is not a database enum"
        values: list[str] = []
        for member in enum:
            values.append(member.value)
        assert sorted(labels[name]) == sorted(values), name
    assert sorted(set(labels) - listed) == [], "database enums with no StrEnum"


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
