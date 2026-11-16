from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Environment(StrEnum):
    """Outside `prod`, registries refuse every provider but the fakes
    (plan-architecture.md §6)."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


@dataclass(frozen=True)
class Settings:
    environment: Environment
    database_url: str


def load_settings(env: Mapping[str, str]) -> Settings:
    """Read settings once at startup, from the environment the caller passes
    (`os.environ`). No module reads the environment on import. A bad value is
    an operator mistake at startup, so it raises with the reason."""
    raw_environment = env.get("ENVIRONMENT", "dev")
    try:
        environment = Environment(raw_environment)
    except ValueError:
        raise ValueError(
            f"ENVIRONMENT must be one of {[e.value for e in Environment]},"
            f" got {raw_environment!r}"
        ) from None
    database_url = env.get("DATABASE_URL", "postgresql:///mor_dev")
    return Settings(environment=environment, database_url=database_url)
