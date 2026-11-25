import pytest

from mor.config import DEFAULT_SELF_URL, Environment, Settings, load_settings


def test_defaults_to_dev_and_the_local_dev_database() -> None:
    assert load_settings({}) == Settings(
        environment=Environment.DEV,
        database_url="postgresql:///mor_dev",
        self_url=DEFAULT_SELF_URL,
        fake_psp_origin="http://localhost:8741",
    )


def test_reads_environment_and_database_url() -> None:
    settings = load_settings(
        {
            "ENVIRONMENT": "prod",
            "DATABASE_URL": "postgresql:///other",
            "SELF_URL": "http://127.0.0.1:9000/",
        }
    )
    assert settings == Settings(
        environment=Environment.PROD,
        database_url="postgresql:///other",
        self_url="http://127.0.0.1:9000",
        fake_psp_origin="http://localhost:9000",
    )


def test_unknown_environment_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        load_settings({"ENVIRONMENT": "production"})
