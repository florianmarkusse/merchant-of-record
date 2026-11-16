import pytest

from mor.config import Environment, Settings, load_settings


def test_defaults_to_dev_and_the_local_dev_database() -> None:
    assert load_settings({}) == Settings(Environment.DEV, "postgresql:///mor_dev")


def test_reads_environment_and_database_url() -> None:
    settings = load_settings(
        {"ENVIRONMENT": "prod", "DATABASE_URL": "postgresql:///other"}
    )
    assert settings == Settings(Environment.PROD, "postgresql:///other")


def test_unknown_environment_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        load_settings({"ENVIRONMENT": "production"})
