import asyncio
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from mor.db.migrate import (
    Migrated,
    MigrationFile,
    MigrationRefused,
    migrate,
    read_migrations,
)


NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)


async def _files(directory: Path) -> list[MigrationFile]:
    files = await read_migrations(directory)
    assert not isinstance(files, MigrationRefused), files
    return files


async def _run(url: str, directory: Path) -> Migrated | MigrationRefused:
    files = await _files(directory)
    async with await psycopg.AsyncConnection.connect(url) as conn:
        return await migrate(conn, files, NOW)


async def _tables(url: str) -> list[str]:
    async with await psycopg.AsyncConnection.connect(url) as conn:
        cur = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " ORDER BY tablename"
        )
        rows = await cur.fetchall()
    names: list[str] = []
    for (name,) in rows:
        assert isinstance(name, str)
        names.append(name)
    return names


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def test_applies_files_in_version_order_once(
    scratch_database_url: str, tmp_path: Path
) -> None:
    # 0002 needs 0001's table: order matters, not the listing order.
    _write(tmp_path, "0002_b.sql", "ALTER TABLE a ADD COLUMN y int;")
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int); CREATE TABLE b (x int);")

    first = asyncio.run(_run(scratch_database_url, tmp_path))
    second = asyncio.run(_run(scratch_database_url, tmp_path))

    assert first == Migrated(("0001_a.sql", "0002_b.sql"))
    assert second == Migrated(())
    assert asyncio.run(_tables(scratch_database_url)) == ["a", "b", "schema_migration"]


def test_new_file_after_applied_ones_is_applied(
    scratch_database_url: str, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int);")
    asyncio.run(_run(scratch_database_url, tmp_path))
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (x int);")

    assert asyncio.run(_run(scratch_database_url, tmp_path)) == Migrated(
        ("0002_b.sql",)
    )


def test_edited_applied_file_is_refused_and_nothing_runs(
    scratch_database_url: str, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int);")
    asyncio.run(_run(scratch_database_url, tmp_path))
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x bigint);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (x int);")

    result = asyncio.run(_run(scratch_database_url, tmp_path))

    assert result == MigrationRefused("checksum_changed", "0001_a.sql")
    assert asyncio.run(_tables(scratch_database_url)) == ["a", "schema_migration"]


def test_deleted_applied_file_is_refused(
    scratch_database_url: str, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int);")
    asyncio.run(_run(scratch_database_url, tmp_path))
    (tmp_path / "0001_a.sql").unlink()

    result = asyncio.run(_run(scratch_database_url, tmp_path))

    assert result == MigrationRefused("applied_file_missing", "0001_a.sql")


def test_badly_named_or_duplicate_files_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "1_a.sql", "")
    assert asyncio.run(read_migrations(tmp_path)) == MigrationRefused(
        "invalid_file_name", "1_a.sql"
    )
    (tmp_path / "1_a.sql").unlink()
    _write(tmp_path, "0001_a.sql", "")
    _write(tmp_path, "0001_b.sql", "")
    assert asyncio.run(read_migrations(tmp_path)) == MigrationRefused(
        "duplicate_version", "0001_b.sql"
    )


def test_failing_file_keeps_earlier_ones_and_applies_nothing_of_itself(
    scratch_database_url: str, tmp_path: Path
) -> None:
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (x int); SELECT 1/0;")

    with pytest.raises(psycopg.errors.DivisionByZero):
        asyncio.run(_run(scratch_database_url, tmp_path))

    assert asyncio.run(_tables(scratch_database_url)) == ["a", "schema_migration"]
    # Fixed by a new file version: the runner picks up where it stopped.
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (x int);")
    assert asyncio.run(_run(scratch_database_url, tmp_path)) == Migrated(
        ("0002_b.sql",)
    )


def test_two_runners_at_once_apply_each_file_once(
    scratch_database_url: str, tmp_path: Path
) -> None:
    # Without the runner's lock both would try CREATE TABLE and one would fail.
    _write(tmp_path, "0001_a.sql", "CREATE TABLE a (x int); SELECT pg_sleep(0.2);")
    _write(tmp_path, "0002_b.sql", "CREATE TABLE b (x int);")

    async def both() -> list[Migrated | MigrationRefused]:
        return list(
            await asyncio.gather(
                _run(scratch_database_url, tmp_path),
                _run(scratch_database_url, tmp_path),
            )
        )

    results = asyncio.run(both())

    assert Migrated(("0001_a.sql", "0002_b.sql")) in results
    assert Migrated(()) in results
