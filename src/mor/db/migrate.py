"""The migration runner: applies `migrations/NNNN_name.sql` in order.

    uv run python -m mor.db.migrate            # DATABASE_URL, default mor_dev

Each file runs in its own transaction with its `schema_migration` row. An
applied file whose checksum changed, or that disappeared, stops the run:
applied migrations are never edited (AGENTS.md), a new one is added.
"""

import asyncio
import hashlib
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from psycopg.rows import class_row

from mor.config import load_settings
from mor.db.connect import Connection, connect


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

# Held for the whole run, so two runners started at once apply each file
# once: the second waits, then finds nothing pending. A session lock, so a
# runner that dies frees it with its connection.
MIGRATION_LOCK_KEY = 7_201_100_001

_FILE_NAME = re.compile(r"([0-9]{4})_([a-z0-9_]+)\.sql")


@dataclass(frozen=True, kw_only=True)
class MigrationFile:
    version: int
    name: str
    sql: str
    sha256: str


@dataclass(frozen=True)
class AppliedMigration:
    version: int
    name: str
    sha256: str


@dataclass(frozen=True)
class Migrated:
    applied: tuple[str, ...]


@dataclass(frozen=True)
class MigrationRefused:
    reason: Literal[
        "invalid_file_name",
        "duplicate_version",
        "checksum_changed",
        "applied_file_missing",
    ]
    detail: str


def _read_files(directory: Path) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.sql")):
        files.append((path.name, path.read_text(encoding="utf-8")))
    return files


async def read_migrations(directory: Path) -> list[MigrationFile] | MigrationRefused:
    """The migration files in version order, or why they cannot be used."""
    raw_files = await asyncio.to_thread(_read_files, directory)
    files: list[MigrationFile] = []
    seen: set[int] = set()
    for file_name, sql in raw_files:
        match = _FILE_NAME.fullmatch(file_name)
        if match is None:
            return MigrationRefused("invalid_file_name", file_name)
        version = int(match.group(1))
        if version in seen:
            return MigrationRefused("duplicate_version", file_name)
        seen.add(version)
        sha256 = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        files.append(
            MigrationFile(version=version, name=file_name, sql=sql, sha256=sha256)
        )
    files.sort(key=lambda f: f.version)
    return files


def pending_migrations(
    files: Sequence[MigrationFile], applied: Sequence[AppliedMigration]
) -> list[MigrationFile] | MigrationRefused:
    """The files still to apply, after checking every applied one is
    unchanged and still present."""
    by_version: dict[int, MigrationFile] = {}
    for file in files:
        by_version[file.version] = file
    applied_versions: set[int] = set()
    for row in applied:
        applied_versions.add(row.version)
        file = by_version.get(row.version)
        if file is None:
            return MigrationRefused("applied_file_missing", row.name)
        if file.sha256 != row.sha256 or file.name != row.name:
            return MigrationRefused("checksum_changed", file.name)
    pending: list[MigrationFile] = []
    for file in files:
        if file.version not in applied_versions:
            pending.append(file)
    return pending


async def migrate(
    conn: Connection, files: Sequence[MigrationFile], now: datetime
) -> Migrated | MigrationRefused:
    """Apply pending files, each in its own committed transaction. A failing
    file raises (a broken migration is a bug); only its own transaction is
    rolled back.
    Unlike persistence functions, this commits: it is a runner, not a step
    inside someone else's transaction."""
    await conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    await conn.commit()
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
              version     int PRIMARY KEY,
              name        text NOT NULL,
              sha256      text NOT NULL,
              applied_at  timestamptz NOT NULL
            )
            """
        )
        async with conn.cursor(row_factory=class_row(AppliedMigration)) as cur:
            await cur.execute(
                "SELECT version, name, sha256 FROM schema_migration ORDER BY version"
            )
            applied = await cur.fetchall()
        await conn.commit()

        pending = pending_migrations(files, applied)
        if isinstance(pending, MigrationRefused):
            return pending

        done: list[str] = []
        for file in pending:
            # bytes, not str: psycopg types its query as LiteralString,
            # which a file's text cannot be. A query without parameters may
            # hold several statements.
            await conn.execute(file.sql.encode("utf-8"))
            await conn.execute(
                """
                INSERT INTO schema_migration (version, name, sha256, applied_at)
                VALUES (%(version)s, %(name)s, %(sha256)s, %(applied_at)s)
                """,
                {
                    "version": file.version,
                    "name": file.name,
                    "sha256": file.sha256,
                    "applied_at": now,
                },
            )
            await conn.commit()
            done.append(file.name)
        return Migrated(tuple(done))
    finally:
        # Ends a failed file's transaction (a no-op after a commit), so the
        # unlock can run; the exception then carries on.
        await conn.rollback()
        await conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
        await conn.commit()


async def _main(directory: Path) -> int:
    settings = load_settings(os.environ)
    files = await read_migrations(directory)
    if isinstance(files, MigrationRefused):
        print(f"refused: {files.reason}: {files.detail}", file=sys.stderr)
        return 1
    async with await connect(settings.database_url) as conn:
        result = await migrate(conn, files, datetime.now(UTC))
    match result:
        case Migrated(applied=()):
            print("up to date")
            return 0
        case Migrated(applied=applied):
            for name in applied:
                print(f"applied {name}")
            return 0
        case MigrationRefused(reason=reason, detail=detail):
            print(f"refused: {reason}: {detail}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(MIGRATIONS_DIR)))
