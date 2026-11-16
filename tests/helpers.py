import psycopg


# The suite's own databases on the local cluster, reached over peer auth as
# the developer's role. Never mor_dev: these are dropped and recreated.
ADMIN_URL = "postgresql:///postgres"
TEST_URL = "postgresql:///mor_test"
SCRATCH_URL = "postgresql:///mor_test_migrations"


async def empty_tables(database_url: str) -> None:
    """Empty every table but the migration record. The server lists the
    tables, so a new one is emptied without being named here."""
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await conn.execute(
            """
            DO $$
            DECLARE tables text;
            BEGIN
              SELECT string_agg(format('%I', tablename), ', ') INTO tables
              FROM pg_tables
              WHERE schemaname = 'public' AND tablename <> 'schema_migration';
              IF tables IS NOT NULL THEN
                EXECUTE 'TRUNCATE ' || tables || ' CASCADE';
              END IF;
            END $$
            """
        )
        await conn.commit()


async def advisory_locks_held(database_url: str) -> int:
    """Advisory locks held by any session: a worker must leave none."""
    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
        )
        row = await cur.fetchone()
    assert row is not None
    count = row[0]
    assert isinstance(count, int)
    return count
