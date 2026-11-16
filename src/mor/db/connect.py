import psycopg
from psycopg.rows import TupleRow


type Connection = psycopg.AsyncConnection[TupleRow]


async def connect(database_url: str, autocommit: bool = False) -> Connection:
    """Autocommit off by default: the caller commits what belongs together (a
    state change, its ledger entry and its facts). A worker's dedicated
    connection uses autocommit and opens each transaction explicitly, so its
    session advisory locks are taken outside any transaction."""
    return await psycopg.AsyncConnection.connect(database_url, autocommit=autocommit)
