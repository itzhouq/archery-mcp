import pytest

from archery_mcp.config import (
    DEFAULT_SCHEMA_NAME,
    ConfigurationError,
    Settings,
)

ENV_KEYS = (
    "ARCHERY_BASE_URL",
    "ARCHERY_USERNAME",
    "ARCHERY_PASSWORD",
    "ARCHERY_TOTP_SECRET",
    "ARCHERY_INSTANCE_NAME",
    "ARCHERY_DB_NAME",
    "ARCHERY_SCHEMA_NAME",
    "ARCHERY_QUERY_LIMIT",
    "ARCHERY_QUERY_MAX_ROWS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_missing_required_env():
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_target_instance_and_db_are_required(monkeypatch):
    monkeypatch.setenv("ARCHERY_BASE_URL", "http://archery.test")
    monkeypatch.setenv("ARCHERY_USERNAME", "u")
    monkeypatch.setenv("ARCHERY_PASSWORD", "p")
    with pytest.raises(ConfigurationError):
        Settings.from_env()

    monkeypatch.setenv("ARCHERY_INSTANCE_NAME", "prod-app-db")
    monkeypatch.setenv("ARCHERY_DB_NAME", "app_db")
    settings = Settings.from_env()

    assert settings.instance_name == "prod-app-db"
    assert settings.db_name == "app_db"
    assert settings.schema_name == DEFAULT_SCHEMA_NAME
    assert settings.query_default_limit == 100
    assert settings.totp_secret == ""


def set_required_env(monkeypatch, base_url="http://archery.test"):
    monkeypatch.setenv("ARCHERY_BASE_URL", base_url)
    monkeypatch.setenv("ARCHERY_USERNAME", "u")
    monkeypatch.setenv("ARCHERY_PASSWORD", "p")
    monkeypatch.setenv("ARCHERY_INSTANCE_NAME", "prod-app-db")
    monkeypatch.setenv("ARCHERY_DB_NAME", "app_db")


def test_base_url_trailing_slash_and_invalid(monkeypatch):
    set_required_env(monkeypatch, base_url="http://archery.test/")
    assert Settings.from_env().base_url == "http://archery.test"

    set_required_env(monkeypatch, base_url="not-a-url")
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_query_limit_is_only_a_default_and_delegates_upper_bound_to_archery(monkeypatch):
    set_required_env(monkeypatch)

    monkeypatch.setenv("ARCHERY_QUERY_LIMIT", "1000")
    assert Settings.from_env().query_default_limit == 1000

    monkeypatch.setenv("ARCHERY_QUERY_LIMIT", "0")
    assert Settings.from_env().query_default_limit == 0

    monkeypatch.setenv("ARCHERY_QUERY_LIMIT", "-1")
    with pytest.raises(ConfigurationError):
        Settings.from_env()

    monkeypatch.setenv("ARCHERY_QUERY_LIMIT", "abc")
    with pytest.raises(ConfigurationError):
        Settings.from_env()
