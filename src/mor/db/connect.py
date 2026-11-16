import psycopg
from psycopg.rows import TupleRow


type Connection = psycopg.AsyncConnection[TupleRow]


async def connect(database_url: str) -> Connection:
    """A connection with autocommit off: the caller commits what belongs
    together (a state change, its ledger entry and its facts)."""
    return await psycopg.AsyncConnection.connect(database_url)
