import asyncio
import os
from datetime import UTC, datetime

import psycopg
import pytest
from hypothesis import settings

from mor.db.migrate import MIGRATIONS_DIR, MigrationRefused, migrate, read_migrations


# Hypothesis profiles (plan-architecture.md §10). "dev" is the small budget
# inside every `uv run pytest`; it first replays every failure saved in the
# git-ignored .hypothesis/. Not derandomized: in Hypothesis that implies
# database=None, which would skip those replays. "soak" is the long run:
# `HYPOTHESIS_PROFILE=soak uv run pytest`. A failure it finds is pinned as an
# @example in the test.
settings.register_profile("dev", max_examples=200)
settings.register_profile("soak", max_examples=100_000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


# The suite's own databases on the local cluster, reached over peer auth as
# the developer's role. Never mor_dev: these are dropped and recreated.
ADMIN_URL = "postgresql:///postgres"
TEST_URL = "postgresql:///mor_test"
SCRATCH_URL = "postgresql:///mor_test_migrations"


async def _recreate_test_database() -> None:
    async with await psycopg.AsyncConnection.connect(
        ADMIN_URL, autocommit=True
    ) as admin:
        await admin.execute("DROP DATABASE IF EXISTS mor_test WITH (FORCE)")
        await admin.execute("CREATE DATABASE mor_test")


async def _recreate_scratch_database() -> None:
    async with await psycopg.AsyncConnection.connect(
        ADMIN_URL, autocommit=True
    ) as admin:
        await admin.execute("DROP DATABASE IF EXISTS mor_test_migrations WITH (FORCE)")
        await admin.execute("CREATE DATABASE mor_test_migrations")


async def _migrate_test_database() -> None:
    files = await read_migrations(MIGRATIONS_DIR)
    assert not isinstance(files, MigrationRefused), files
    async with await psycopg.AsyncConnection.connect(TEST_URL) as conn:
        result = await migrate(conn, files, datetime.now(UTC))
    assert not isinstance(result, MigrationRefused), result


async def _empty_tables() -> None:
    # Every table but the migration record, found by the server, so a new
    # table is emptied without being listed here.
    async with await psycopg.AsyncConnection.connect(TEST_URL) as conn:
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


@pytest.fixture(scope="session")
def migrated_test_database() -> str:
    """mor_test, created empty once per session and migrated with the real
    migrations/ files: this is also the test that they apply cleanly."""
    asyncio.run(_recreate_test_database())
    asyncio.run(_migrate_test_database())
    return TEST_URL


@pytest.fixture
def database_url(migrated_test_database: str) -> str:
    """The test database with every table empty. Tests commit for real, so
    commit-time checks (the ledger's balance trigger) run as in prod."""
    asyncio.run(_empty_tables())
    return migrated_test_database


@pytest.fixture
def scratch_database_url() -> str:
    """An empty database with no migrations, for the runner's own tests."""
    asyncio.run(_recreate_scratch_database())
    return SCRATCH_URL
