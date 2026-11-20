from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Environment(StrEnum):
    """Which provider accounts may be used: fakes in `dev`, fakes and a
    vendor's sandbox in `test`, live accounts in `prod`
    (plan-architecture.md §6, `mor.provider_accounts`)."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


DEFAULT_SELF_URL = "http://127.0.0.1:8741"


@dataclass(frozen=True)
class Settings:
    environment: Environment
    database_url: str
    # Our own address as seen from this machine: where the server listens,
    # where seeded accounts reach the fakes, and where the fakes send their
    # webhooks (docs/plan-test-setup.md §2).
    self_url: str


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
    self_url = env.get("SELF_URL", DEFAULT_SELF_URL).rstrip("/")
    return Settings(environment, database_url, self_url)
