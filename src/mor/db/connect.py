import psycopg
from psycopg.rows import TupleRow

from mor.db.enums import register_enums


type Connection = psycopg.AsyncConnection[TupleRow]

# Every connection of ours, and the fakes', reads moments in UTC.
SESSION_OPTIONS = "-c TimeZone=UTC"


async def connect(database_url: str, autocommit: bool = False) -> Connection:
    """Autocommit off by default: the caller commits what belongs together (a
    state change, its ledger entry and its facts). A worker's dedicated
    connection uses autocommit and opens each transaction explicitly, so its
    session advisory locks are taken outside any transaction.

    The session's time zone is UTC, whatever the server's: a `timestamptz`
    is stored as an absolute moment, but read back in the session's zone,
    and every moment inside the process is UTC (`mor.dates`)."""
    conn = await psycopg.AsyncConnection.connect(
        database_url, autocommit=autocommit, options=SESSION_OPTIONS
    )
    await register_enums(conn)
    if not autocommit:
        # The catalog read opened a transaction; the caller's work starts
        # its own, as it would on a fresh connection.
        await conn.rollback()
    return conn
