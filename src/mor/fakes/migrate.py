"""Apply the fakes' own migrations (`migrations_fake/`), outside prod only.

    uv run python -m mor.fakes.migrate         # DATABASE_URL, default mor_dev

The same runner as ours, on a connection whose search_path is the
`fake_vendor` schema: the files and the runner's `schema_migration` record
land there, apart from our tables, with no SQL composed at runtime.
"""

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from mor.config import Environment, load_settings
from mor.db.migrate import Migrated, MigrationRefused, migrate, read_migrations


FAKE_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations_fake"


@dataclass(frozen=True)
class FakesRefusedInProd:
    """The fakes never exist in prod (plan-architecture.md §6)."""


async def migrate_fakes(
    database_url: str, environment: Environment, now: datetime
) -> Migrated | MigrationRefused | FakesRefusedInProd:
    if environment == Environment.PROD:
        return FakesRefusedInProd()
    files = await read_migrations(FAKE_MIGRATIONS_DIR)
    if isinstance(files, MigrationRefused):
        return files
    async with await psycopg.AsyncConnection.connect(
        database_url, options="-c search_path=fake_vendor"
    ) as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS fake_vendor")
        await conn.commit()
        return await migrate(conn, files, now)


async def _main() -> int:
    settings = load_settings(os.environ)
    result = await migrate_fakes(
        settings.database_url, settings.environment, datetime.now(UTC)
    )
    match result:
        case Migrated(applied=applied):
            for name in applied:
                print(f"applied fake {name}")
            if not applied:
                print("fakes up to date")
            return 0
        case MigrationRefused(reason=reason, detail=detail):
            print(f"refused: {reason}: {detail}", file=sys.stderr)
            return 1
        case FakesRefusedInProd():
            print("refused: the fakes are never migrated in prod", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
