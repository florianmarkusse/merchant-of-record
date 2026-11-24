"""The stored shape of every fact version (`fact_schemas.json`), and the
command that records a new version's:

    uv run python -m tests.messaging.fact_schemas

A fact waits in the outbox as JSON until it is delivered, and is decoded
with the record the code has then, refusing unknown fields. A field added
to or removed from an existing version leaves its stored messages
undecodable, so a shape change is a new version (`messaging.md`). The
command adds the shape of a version that is new and drops one that is
gone, but never rewrites the shape of a version that exists:
`tests/messaging/test_fact_schemas.py` fails on that until it is a new
version instead."""

import json
import sys
from pathlib import Path
from typing import get_args

import msgspec

from mor.messaging.facts import Fact


SNAPSHOT = Path(__file__).resolve().parent / "fact_schemas.json"

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None


def _without_docstrings(node: Json) -> Json:
    """A docstring is no part of the shape: msgspec puts it in each
    schema's `description`. A field named `description` holds a schema
    (a dict), so only text values are dropped."""
    if isinstance(node, dict):
        kept: dict[str, Json] = {}
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                continue
            kept[key] = _without_docstrings(value)
        return kept
    if isinstance(node, list):
        items: list[Json] = []
        for item in node:
            items.append(_without_docstrings(item))
        return items
    return node


def current_schemas() -> dict[str, Json]:
    """Each fact version's JSON schema as the code has it now, keyed
    `message_type/vN`."""
    schemas: dict[str, Json] = {}
    for record in get_args(Fact.__value__):
        key = f"{record.message_type}/v{record.schema_version}"
        # Plain JSON, read back untyped: msgspec cannot decode into a
        # recursive type such as Json.
        schema: Json = msgspec.json.decode(
            msgspec.json.encode(msgspec.json.schema(record))
        )
        schemas[key] = _without_docstrings(schema)
    return schemas


def stored_schemas() -> dict[str, Json]:
    if not SNAPSHOT.exists():
        return {}
    stored: dict[str, Json] = msgspec.json.decode(SNAPSHOT.read_bytes())
    return stored


def _write(schemas: dict[str, Json]) -> None:
    SNAPSHOT.write_text(json.dumps(schemas, indent=2, sort_keys=True) + "\n")


def changed_versions(current: dict[str, Json], stored: dict[str, Json]) -> list[str]:
    """The versions in both whose shape differs."""
    changed: list[str] = []
    for key in sorted(current):
        if key in stored and stored[key] != current[key]:
            changed.append(key)
    return changed


def main() -> int:
    current = current_schemas()
    stored = stored_schemas()
    changed = changed_versions(current, stored)
    if changed:
        print("refused: these versions changed shape; make a new version:")
        for key in changed:
            print(f"  {key}")
        return 1
    for key in sorted(current):
        if key not in stored:
            print(f"added {key}")
    for key in sorted(stored):
        if key not in current:
            print(f"dropped {key}")
    _write(current)
    return 0


if __name__ == "__main__":
    sys.exit(main())
