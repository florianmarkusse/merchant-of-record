"""Reads of a row that must exist: an aggregate, an `INSERT … RETURNING`,
or a row a foreign key, a lock or the caller's own write guarantees. No
row is a bug, so it raises instead of returning `None`.

A read that may find nothing (a web request's id, a lookup) keeps
`fetchone()` and handles `None` at the call site."""

from typing import LiteralString

from psycopg import AsyncCursor
from psycopg.abc import Params

from mor.db.connect import Connection


class MissingRow(Exception):
    """A query that must find a row found none: a bug."""


async def one_row[Row](cur: AsyncCursor[Row], missing: str) -> Row:
    """The row the cursor's query must have found; `missing` says what
    was looked for."""
    row = await cur.fetchone()
    if row is None:
        raise MissingRow(missing)
    return row


async def one_value[T](
    conn: Connection, query: LiteralString, params: Params, kind: type[T]
) -> T:
    """The single non-null value of a one-column query that must find a
    row, checked against `kind` (psycopg reads columns untyped)."""
    cur = await conn.execute(query, params)
    row = await cur.fetchone()
    if row is None:
        raise MissingRow(query)
    value = row[0]
    if not isinstance(value, kind):
        raise TypeError(f"{query!r} gave {value!r}, not {kind.__name__}")
    return value
