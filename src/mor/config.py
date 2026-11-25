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


@dataclass(frozen=True, kw_only=True)
class Settings:
    environment: Environment
    database_url: str
    # Our own address as seen from this machine: where the server listens,
    # where seeded accounts reach the fakes, and where the fakes send their
    # webhooks (docs/plan-test-setup.md §2).
    self_url: str
    # Where a browser reaches the fake PSP's card fields: the same server
    # under another origin, so the CSP must allow it as it would a real
    # PSP's (docs/plan-payments.md §4, card fields). Unused in prod.
    fake_psp_origin: str


def fake_origin_for(self_url: str) -> str:
    """`127.0.0.1` beside `localhost`: two origins, one server. Another
    host has no such twin, and the fake then shares our origin."""
    return self_url.replace("//127.0.0.1", "//localhost", 1)


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
    fake_psp_origin = env.get(
        "FAKE_PSP_BROWSER_ORIGIN", fake_origin_for(self_url)
    ).rstrip("/")
    return Settings(
        environment=environment,
        database_url=database_url,
        self_url=self_url,
        fake_psp_origin=fake_psp_origin,
    )
