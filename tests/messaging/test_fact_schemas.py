"""A shape change is a new version (`messaging.md`): an existing fact
version keeps the shape its stored messages were written in
(`fact_schemas.py` says why and how to record a new one)."""

from tests.messaging.fact_schemas import (
    changed_versions,
    current_schemas,
    stored_schemas,
)


def test_no_fact_version_changed_its_stored_shape() -> None:
    changed = changed_versions(current_schemas(), stored_schemas())
    assert not changed, (
        "these versions changed shape; add a new version instead "
        "(ProbeStartedV2) and keep the old one decodable: " + ", ".join(changed)
    )


def test_every_fact_version_has_its_shape_recorded() -> None:
    current = current_schemas()
    stored = stored_schemas()
    missing: list[str] = []
    for key in sorted(current):
        if key not in stored:
            missing.append(key)
    gone: list[str] = []
    for key in sorted(stored):
        if key not in current:
            gone.append(key)
    assert not missing and not gone, (
        "run `uv run python -m tests.messaging.fact_schemas` to record "
        f"new versions {missing} and drop retired ones {gone}"
    )
