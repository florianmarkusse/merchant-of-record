"""Load `docs/markets/` into the database as a new versioned set
(docs/plan-quote.md §2).

    uv run python -m mor.markets.load            # show what would change
    uv run python -m mor.markets.load --write    # and write it

DATABASE_URL as everywhere (default mor_dev). Loading files equal to the
latest load writes nothing. The git commit is stored with the load, with
`+uncommitted` when docs/markets/ has changes not yet committed.
"""

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from mor.audit import Actor, ActorKind
from mor.config import load_settings
from mor.db.connect import connect
from mor.markets.changes import describe_changes
from mor.markets.records import MarketSet
from mor.markets.source import parse, read_texts
from mor.markets.store import Loaded, Unchanged, latest_load, load_market, read_market


MARKETS_DIR = Path(__file__).resolve().parents[3] / "docs" / "markets"
LOADER = Actor(ActorKind.SYSTEM, "markets.load")


def git_version(directory: Path) -> str:
    """The commit the files come from, or `unknown` outside a git checkout."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(directory), "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown"
    return f"{commit}+uncommitted" if dirty else commit


async def _main(directory: Path, write: bool) -> int:
    texts = read_texts(directory)
    if isinstance(texts, list):
        for error in texts:
            print(error)
        return 1
    market = parse(texts)
    if isinstance(market, list):
        for error in market:
            print(error)
        print(f"{len(market)} errors: nothing loaded")
        return 1
    settings = load_settings(os.environ)
    async with await connect(settings.database_url) as conn:
        previous = await latest_load(conn)
        old: MarketSet | None = None
        if previous is not None:
            old = await read_market(conn, previous.id)
        changes = describe_changes(old, market)
        if not write:
            await conn.rollback()
            _print_changes(changes, previous is not None)
            print("dry run: run again with --write to load")
            return 0
        result = await load_market(
            conn, market, git_version(directory), LOADER, datetime.now(UTC)
        )
        await conn.commit()
    match result:
        case Unchanged(load_id=load_id):
            print(f"unchanged: load {load_id} already holds these files")
        case Loaded(load_id=load_id):
            _print_changes(changes, previous is not None)
            print(f"wrote load {load_id}")
    return 0


def _print_changes(changes: list[str], had_previous: bool) -> None:
    if not changes and had_previous:
        print("no changes against the load in force")
    for line in changes:
        print(line)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load docs/markets/ as a new set")
    parser.add_argument("--write", action="store_true", help="write the load")
    parser.add_argument("--dir", type=Path, default=MARKETS_DIR)
    arguments = parser.parse_args()
    sys.exit(asyncio.run(_main(arguments.dir, arguments.write)))
